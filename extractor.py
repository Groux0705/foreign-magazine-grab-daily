"""Article extraction: turn a TIME article page into a clean reading view.

Primary engine: trafilatura (best-in-class open-source extractor).
Fallback engine: readability-lxml (kept for resilience on odd pages).
Last-resort engine: Jina Reader markdown fed into ``extract_from_markdown``
(wired up from ``scraper.py`` when the local extractors produce too little
content).

All engines converge on the same sanitized HTML shape so the rest of the
pipeline (asset localization, annotations, highlights) doesn't care how the
text was obtained.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from readability import Document

try:
    import trafilatura
    from trafilatura.settings import use_config as _traf_use_config
    _TRAF_CONFIG = _traf_use_config()
    # Let trafilatura keep short paragraphs; TIME has a lot of 1-sentence
    # pull-quotes and transitional lines we don't want to drop.
    _TRAF_CONFIG.set("DEFAULT", "MIN_EXTRACTED_SIZE", "200")
    _TRAF_CONFIG.set("DEFAULT", "MIN_OUTPUT_SIZE", "200")
except Exception:  # pragma: no cover - import guard
    trafilatura = None
    _TRAF_CONFIG = None

try:
    import markdown as _markdown_lib
except Exception:  # pragma: no cover
    _markdown_lib = None

log = logging.getLogger("time_scraper")

ALLOWED_TAGS = {
    "p", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "em", "strong",
    "i", "b", "a", "figure", "figcaption", "img", "br", "hr",
}
STRIP_TAGS = {
    "script", "style", "iframe", "noscript", "svg", "button", "form", "input",
    "nav", "aside", "footer", "header",
}
BAD_TOKENS = {
    "ad", "ads", "advert", "advertisement", "promo", "promotion",
    "recommend", "recommended", "recommendations", "related",
    "newsletter", "subscribe", "subscription", "social", "share",
    "comments", "comment", "video", "paywall", "sponsored", "sponsor",
    "outbrain", "taboola", "toolbar", "breadcrumb", "breadcrumbs",
    "author-bio", "author-card", "inline-signup",
}
AD_TEXT_RE = re.compile(r"^(advertisement|advertisment|sponsored|promoted content)\s*$", re.I)


def _token_set(value: str) -> set[str]:
    if not value:
        return set()
    out: set[str] = set()
    for token in value.split():
        token = token.strip().lower()
        if token:
            out.add(token)
    return out


def _has_bad_token(value: str) -> bool:
    if not value:
        return False
    tokens = _token_set(value)
    if tokens & BAD_TOKENS:
        return True
    for t in tokens:
        parts = t.split("-")
        if any(p in {"ad", "ads", "advert", "promo"} and i == 0 for i, p in enumerate(parts)):
            return True
        for bad in ("related", "newsletter", "paywall", "sponsored", "taboola",
                    "outbrain", "recommend", "share", "social-share"):
            if bad in parts:
                return True
    return False


ALLOWED_ATTRS_GLOBAL: set[str] = set()
ALLOWED_ATTRS_PER_TAG = {
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height"},
}


def _detached(el) -> bool:
    return getattr(el, "attrs", None) is None


def _looks_like_boilerplate(el) -> bool:
    classes = " ".join(el.get("class") or [])
    idval = el.get("id") or ""
    if _has_bad_token(classes):
        return True
    if _has_bad_token(idval):
        return True
    text = el.get_text(" ", strip=True)
    if text and AD_TEXT_RE.match(text):
        return True
    return False


def _sanitize(soup: BeautifulSoup, base_url: str) -> None:
    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    for el in list(soup.find_all(True)):
        if _detached(el):
            continue
        if _looks_like_boilerplate(el):
            el.decompose()

    for el in list(soup.find_all(True)):
        if _detached(el):
            continue
        if el.name not in ALLOWED_TAGS:
            el.unwrap()

    for el in soup.find_all(True):
        if _detached(el):
            continue
        allowed = ALLOWED_ATTRS_PER_TAG.get(el.name, set()) | ALLOWED_ATTRS_GLOBAL
        for attr in list(el.attrs):
            if attr not in allowed:
                del el.attrs[attr]
        if el.name == "a" and el.get("href"):
            el["href"] = urljoin(base_url, el["href"])
            el["target"] = "_blank"
            el["rel"] = "noopener"
        if el.name == "img" and el.get("src"):
            el["src"] = urljoin(base_url, el["src"])
            el.attrs.pop("width", None)
            el.attrs.pop("height", None)

    for el in list(soup.find_all(["p", "figure", "li", "h2", "h3"])):
        if _detached(el):
            continue
        if not el.get_text(strip=True) and not el.find("img"):
            el.decompose()


def _to_plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    text_parts = []
    for el in soup.find_all(["p", "h2", "h3", "li", "blockquote", "figcaption"]):
        t = el.get_text(" ", strip=True)
        if t:
            text_parts.append(t)
    return "\n\n".join(text_parts)


def _word_count(plain: str) -> int:
    return len(re.findall(r"\b[\w']+\b", plain, flags=re.UNICODE))


def _finalize(raw_html: str, base_url: str) -> tuple[str, str, int]:
    """Run sanitizer + plain-text extraction on any engine's raw HTML."""
    soup = BeautifulSoup(raw_html or "", "lxml")
    _sanitize(soup, base_url=base_url)
    body = soup.body or soup
    cleaned = "".join(str(c) for c in body.contents).strip()
    plain = _to_plain_text(cleaned)
    return cleaned, plain, _word_count(plain)


# ---------- engines ----------

def _extract_with_trafilatura(html: str, url: str) -> str:
    if trafilatura is None:
        return ""
    try:
        out = trafilatura.extract(
            html,
            url=url,
            output_format="html",
            include_comments=False,
            include_tables=False,
            include_images=True,
            include_links=True,
            favor_recall=True,
            config=_TRAF_CONFIG,
        )
        return out or ""
    except Exception as exc:  # pragma: no cover
        log.warning("trafilatura failed for %s: %s", url, exc)
        return ""


def _extract_with_readability(html: str) -> str:
    try:
        doc = Document(html)
        return doc.summary(html_partial=True) or ""
    except Exception as exc:  # pragma: no cover
        log.warning("readability failed: %s", exc)
        return ""


# ---------- public API ----------

def _read_meta(full: BeautifulSoup) -> dict:
    def meta(prop=None, name=None):
        sel = {"property": prop} if prop else {"name": name}
        tag = full.find("meta", attrs=sel)
        return tag["content"].strip() if tag and tag.get("content") else None

    title = meta(prop="og:title") or (
        full.title.string.strip() if full.title and full.title.string else ""
    )
    return {
        "title": title,
        "description": meta(prop="og:description") or meta(name="description"),
        "cover_image": meta(prop="og:image"),
        "author": meta(name="author") or meta(prop="article:author"),
        "published_at": meta(prop="article:published_time") or meta(name="pubdate"),
    }


def extract_article(html: str, *, url: str) -> dict:
    """Return ``{title, content_html, plain_text, word_count, cover_image,
    description, author, published_at, engine}``.

    Tries trafilatura first; if the result is suspiciously short, falls back
    to readability-lxml. The caller (``scraper.py``) may further fall back to
    Jina Reader when even the readability path produces too little.
    """
    full = BeautifulSoup(html, "lxml")
    meta = _read_meta(full)

    # Engine 1: trafilatura
    raw = _extract_with_trafilatura(html, url)
    engine = "trafilatura"
    content_html, plain_text, word_count = _finalize(raw, url)

    # Engine 2: readability fallback if trafilatura came up short/empty.
    # We use an absolute floor of 200 words; below that, try readability and
    # keep whichever engine won on word count.
    if word_count < 200:
        rd_raw = _extract_with_readability(html)
        rd_html, rd_plain, rd_wc = _finalize(rd_raw, url)
        if rd_wc > word_count:
            content_html, plain_text, word_count = rd_html, rd_plain, rd_wc
            engine = "readability"

    # Backfill title via readability's short_title if og:title missing.
    if not meta["title"]:
        try:
            meta["title"] = (Document(html).short_title() or "").strip()
        except Exception:
            pass

    return {
        **meta,
        "title": meta["title"] or "",
        "content_html": content_html,
        "plain_text": plain_text,
        "word_count": word_count,
        "engine": engine,
    }


def extract_from_markdown(md_text: str, *, url: str, fallback_title: str = "") -> dict:
    """Build the same article dict from a markdown blob (e.g. Jina Reader).

    Jina Reader returns a header block (``Title:``, ``URL Source:``,
    ``Published Time:``) followed by ``Markdown Content:`` and then the body.
    We parse the header, convert the body with python-markdown, then run it
    through the same sanitizer so images/links behave like the HTML path.
    """
    if not md_text:
        return {
            "title": fallback_title,
            "description": None,
            "cover_image": None,
            "author": None,
            "published_at": None,
            "content_html": "",
            "plain_text": "",
            "word_count": 0,
            "engine": "jina-empty",
        }

    title = fallback_title
    published_at = None
    body = md_text

    # header parsing (best-effort; Jina is consistent about these keys)
    header_match = re.split(r"(?mi)^Markdown Content:\s*$", md_text, maxsplit=1)
    if len(header_match) == 2:
        header, body = header_match
        for line in header.splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if key == "title" and val:
                title = val
            elif key == "published time" and val:
                published_at = val

    if _markdown_lib is None:
        # degrade to plain text if markdown package is unavailable
        plain = body.strip()
        return {
            "title": title,
            "description": None,
            "cover_image": None,
            "author": None,
            "published_at": published_at,
            "content_html": "<p>" + plain.replace("\n\n", "</p><p>") + "</p>",
            "plain_text": plain,
            "word_count": _word_count(plain),
            "engine": "jina-plain",
        }

    raw_html = _markdown_lib.markdown(body, extensions=["extra"])
    content_html, plain_text, word_count = _finalize(raw_html, url)

    return {
        "title": title,
        "description": None,
        "cover_image": None,
        "author": None,
        "published_at": published_at,
        "content_html": content_html,
        "plain_text": plain_text,
        "word_count": word_count,
        "engine": "jina",
    }
