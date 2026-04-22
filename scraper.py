"""TIME magazine daily full-text scraper.

For each day, fetches the top 3 articles from Health / Tech / Business
section pages, extracts their reading view via readability-lxml, downloads
images to data/assets/<article_id>/, and upserts everything into SQLite.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import db
import extractor

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ASSETS_DIR = DATA_DIR / "assets"
LOG_DIR = ROOT / "logs"

CATEGORIES = {
    "health": {
        "url": "https://time.com/section/health/",
        "label": "Health",
        "label_cn": "健康",
    },
    "tech": {
        "url": "https://time.com/section/tech/",
        "label": "Technology",
        "label_cn": "科技",
    },
    "business": {
        "url": "https://time.com/section/business/",
        "label": "Business",
        "label_cn": "商业",
    },
}

# Below this word count the local extractor is considered "stub" and we ask
# Jina Reader for a second opinion. TIME articles are typically 400-2000 words,
# so 300 comfortably flags truncated pulls without false-positiving on genuine
# short posts (livestream notices, TIME100 event cards, etc.).
MIN_GOOD_WORD_COUNT = 300

# r.jina.ai returns markdown with YAML-like headers (Title/URL/Published Time)
# followed by "Markdown Content:" and the body. Free tier, no API key needed.
JINA_READER_PREFIX = "https://r.jina.ai/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

IMAGE_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://time.com/",
}

_ARTICLE_HOST_RE = re.compile(r"^https?://(www\.)?time\.com/")
_NON_ARTICLE_RE = re.compile(
    r"^https?://(www\.)?time\.com/"
    r"(section|tag|author|videos?|newsletters?|magazine|subscribe|"
    r"profile|about|contact|search|podcasts?|category)(/|$)"
)


def _setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("time_scraper")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(LOG_DIR / "scraper.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


log = _setup_logger()


def _is_article_url(url: str) -> bool:
    if not url or not _ARTICLE_HOST_RE.match(url):
        return False
    if _NON_ARTICLE_RE.match(url):
        return False
    path = url.split("time.com/", 1)[-1].strip("/")
    return len(path) > 2


def _fetch(url: str, *, timeout: int = 20, binary: bool = False):
    last_err = None
    headers = IMAGE_HEADERS if binary else HEADERS
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.content if binary else resp.text
            log.warning("HTTP %s for %s (attempt %s)", resp.status_code, url, attempt + 1)
        except requests.RequestException as exc:
            last_err = exc
            log.warning("Fetch error %s for %s (attempt %s)", exc, url, attempt + 1)
        time.sleep(1.2 * (attempt + 1))
    if last_err:
        log.error("Giving up on %s: %s", url, last_err)
    return None


def _fetch_via_jina(url: str, *, timeout: int = 30) -> Optional[str]:
    """Ask Jina Reader to extract the URL's main content as markdown.

    Free service, no key required. We keep a single attempt with a generous
    timeout (rendering is server-side) and swallow failures — the caller
    already has a local result it can keep.
    """
    jina_url = JINA_READER_PREFIX + url
    try:
        resp = requests.get(
            jina_url,
            headers={
                "User-Agent": HEADERS["User-Agent"],
                # Ask for plain markdown response rather than streaming JSON.
                "Accept": "text/plain, text/markdown;q=0.9, */*;q=0.5",
                "X-Return-Format": "markdown",
            },
            timeout=timeout,
        )
        if resp.status_code == 200 and resp.text:
            return resp.text
        log.warning("Jina Reader HTTP %s for %s", resp.status_code, url)
    except requests.RequestException as exc:
        log.warning("Jina Reader error %s for %s", exc, url)
    return None


def parse_listing(html: str, limit: int = 3) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    results: list[dict] = []
    for a in soup.select("h2 a, h3 a"):
        href = (a.get("href") or "").strip()
        title = a.get_text(strip=True)
        if not href or not title:
            continue
        if not _is_article_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        results.append({"url": href, "title": title})
        if len(results) >= limit:
            break
    return results


# ---------- asset localization ----------

def _filename_for(url: str, default_ext: str = ".jpg", *, content_type: str = None) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    parsed = urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if not ext or len(ext) > 5:
        if content_type:
            guess = mimetypes.guess_extension(content_type.split(";")[0].strip())
            ext = guess or default_ext
        else:
            ext = default_ext
    # normalize weird extensions
    if ext == ".jpe":
        ext = ".jpg"
    return f"{h}{ext}"


def _download_image(url: str, dest_dir: Path) -> Optional[str]:
    try:
        # do one HEAD-less GET; follow redirects
        resp = requests.get(url, headers=IMAGE_HEADERS, timeout=20, stream=True)
        if resp.status_code != 200:
            log.warning("Image HTTP %s for %s", resp.status_code, url)
            return None
        ct = resp.headers.get("Content-Type", "")
        fname = _filename_for(url, content_type=ct)
        dest = dest_dir / fname
        if not dest.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=16 * 1024):
                    if chunk:
                        f.write(chunk)
        return fname
    except requests.RequestException as exc:
        log.warning("Image fetch error %s for %s", exc, url)
        return None


def localize_assets(article_id: int, content_html: str, cover_url: Optional[str]) -> tuple[str, Optional[str]]:
    """Download images and rewrite src attributes. Returns (new_html, local_cover_path)."""
    asset_dir = ASSETS_DIR / str(article_id)
    soup = BeautifulSoup(content_html, "lxml")

    for img in soup.find_all("img"):
        src = img.get("src")
        if not src or src.startswith("/static/"):
            continue
        if not src.startswith("http"):
            continue
        fname = _download_image(src, asset_dir)
        if fname:
            img["src"] = f"/static/assets/{article_id}/{fname}"
            img["loading"] = "lazy"
        else:
            # if we can't download, drop the img to keep offline-only
            img.decompose()

    new_html = "".join(str(c) for c in (soup.body or soup).contents).strip()

    local_cover: Optional[str] = None
    if cover_url and cover_url.startswith("http"):
        fname = _download_image(cover_url, asset_dir)
        if fname:
            local_cover = f"/static/assets/{article_id}/{fname}"

    return new_html, local_cover


# ---------- main pipeline ----------

def _extract_best(url: str, html: Optional[str]) -> Optional[dict]:
    """Run local extractors; if the result is too short, try Jina Reader.

    Returns the engine output with the highest word count, or ``None`` if
    nothing produced any content.
    """
    local: Optional[dict] = None
    if html:
        try:
            local = extractor.extract_article(html, url=url)
        except Exception as exc:
            log.exception("Local extract failed for %s: %s", url, exc)
            local = None

    local_wc = local["word_count"] if local else 0
    if local_wc >= MIN_GOOD_WORD_COUNT:
        return local

    # Either local failed outright or came back suspiciously short. Ask Jina.
    log.info(
        "  local extractor gave %d words for %s — trying Jina Reader",
        local_wc, url,
    )
    md = _fetch_via_jina(url)
    jina: Optional[dict] = None
    if md:
        try:
            jina = extractor.extract_from_markdown(
                md, url=url,
                fallback_title=(local or {}).get("title", "") if local else "",
            )
        except Exception as exc:
            log.exception("Jina markdown parse failed for %s: %s", url, exc)
            jina = None

    jina_wc = jina["word_count"] if jina else 0

    # Pick the winner, preserving the richer metadata set from the local
    # extractor (og:* tags) when Jina wins on body length.
    if jina_wc > local_wc and jina:
        if local:
            for key in ("title", "description", "cover_image", "author",
                        "published_at"):
                if not jina.get(key) and local.get(key):
                    jina[key] = local[key]
        return jina
    return local


def fetch_and_store_article(url: str, category: str, *, fetch_date: str) -> Optional[dict]:
    html = _fetch(url)
    parsed = _extract_best(url, html)
    if not parsed:
        log.error("All extractors failed for %s", url)
        return None

    if not parsed["content_html"]:
        log.warning("Empty content_html for %s (engine=%s)", url, parsed.get("engine"))

    # First upsert to get an id (needed for asset dir naming).
    article_row = {
        "source_url": url,
        "category": category,
        "title": parsed["title"] or "(untitled)",
        "author": parsed["author"],
        "published_at": parsed["published_at"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_date": fetch_date,
        "description": parsed["description"],
        "cover_image": parsed["cover_image"],  # still remote, will rewrite below
        "content_html": parsed["content_html"],
        "plain_text": parsed["plain_text"],
        "word_count": parsed["word_count"],
    }
    article_id = db.upsert_article(article_row)

    local_html, local_cover = localize_assets(
        article_id, parsed["content_html"], parsed["cover_image"]
    )
    article_row["content_html"] = local_html
    article_row["cover_image"] = local_cover or parsed["cover_image"]
    db.upsert_article(article_row)

    log.info(
        "  saved id=%s engine=%s title=%r (%d words)",
        article_id, parsed.get("engine"), article_row["title"][:50],
        article_row["word_count"],
    )
    return db.get_article(article_id)


def scrape_category(key: str, *, limit: int = 3, fetch_date: str) -> list[dict]:
    conf = CATEGORIES[key]
    log.info("Scraping %s: %s", conf["label"], conf["url"])
    html = _fetch(conf["url"])
    if not html:
        log.error("No listing HTML for %s", key)
        return []
    items = parse_listing(html, limit=limit)
    out: list[dict] = []
    for item in items:
        try:
            row = fetch_and_store_article(item["url"], key, fetch_date=fetch_date)
            if row:
                out.append(row)
        except Exception as exc:
            log.exception("Failed to fetch %s: %s", item["url"], exc)
        time.sleep(0.8)
    log.info("  -> %s articles stored for %s", len(out), key)
    return out


def run(date_str: Optional[str] = None, limit_per_category: int = 3) -> dict:
    db.init_db()
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    log.info("=== Scrape run for %s ===", date_str)
    all_rows: list[dict] = []
    for key in CATEGORIES:
        try:
            all_rows.extend(scrape_category(key, limit=limit_per_category, fetch_date=date_str))
        except Exception as exc:
            log.exception("Failed to scrape %s: %s", key, exc)
    log.info("Done. %s articles total for %s", len(all_rows), date_str)
    return {"date": date_str, "count": len(all_rows), "articles": all_rows}


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
