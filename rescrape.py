"""Backfill script: re-extract existing articles with the new engine stack.

Use this after switching extractor.py to trafilatura + Jina Reader so that
articles ingested under the old readability-only pipeline get their full
bodies filled in.

Examples
--------
    # dry run — print what would change, touch nothing
    python rescrape.py --max-words 400

    # rewrite all articles whose stored word_count is below 400
    python rescrape.py --max-words 400 --apply

    # rewrite just one article by id
    python rescrape.py --ids 19 21 --apply

    # rewrite everything (useful after the extractor improves)
    python rescrape.py --all --apply
"""
from __future__ import annotations

import argparse
import time
from typing import Iterable

import db
import scraper


def _fetch_candidates(*, ids: list[int] | None, max_words: int,
                      all_rows: bool) -> list[dict]:
    with db.get_conn() as c:
        if ids:
            placeholders = ",".join("?" * len(ids))
            rows = c.execute(
                f"SELECT id, source_url, category, fetch_date, word_count, title "
                f"FROM articles WHERE id IN ({placeholders}) ORDER BY id",
                ids,
            ).fetchall()
        elif all_rows:
            rows = c.execute(
                "SELECT id, source_url, category, fetch_date, word_count, title "
                "FROM articles ORDER BY id"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, source_url, category, fetch_date, word_count, title "
                "FROM articles WHERE word_count < ? ORDER BY id",
                (max_words,),
            ).fetchall()
    return rows


def _rewrite_one(row: dict, *, apply: bool, sleep: float) -> tuple[int, int, str]:
    """Returns (old_word_count, new_word_count, engine). Engine='skip' means
    no improvement and we left the row alone."""
    url = row["source_url"]
    html = scraper._fetch(url)
    parsed = scraper._extract_best(url, html)
    if not parsed:
        return row["word_count"], 0, "failed"

    new_wc = parsed["word_count"]
    engine = parsed.get("engine", "?")

    # Only write back when we actually recovered more text — avoid clobbering
    # a good old row if the new engines happen to regress on some edge case.
    if new_wc <= row["word_count"]:
        return row["word_count"], new_wc, f"skip({engine})"

    if not apply:
        return row["word_count"], new_wc, f"would-write({engine})"

    existing = db.get_article(row["id"])
    article_row = {
        "source_url": url,
        "category": row["category"],
        "title": parsed["title"] or existing.get("title") or "(untitled)",
        "author": parsed.get("author") or existing.get("author"),
        "published_at": parsed.get("published_at") or existing.get("published_at"),
        "fetched_at": existing.get("fetched_at"),
        "fetch_date": row["fetch_date"],
        "description": parsed.get("description") or existing.get("description"),
        "cover_image": parsed.get("cover_image") or existing.get("cover_image"),
        "content_html": parsed["content_html"],
        "plain_text": parsed["plain_text"],
        "word_count": new_wc,
    }
    article_id = db.upsert_article(article_row)

    local_html, local_cover = scraper.localize_assets(
        article_id, parsed["content_html"], parsed.get("cover_image")
    )
    article_row["content_html"] = local_html
    article_row["cover_image"] = local_cover or article_row["cover_image"]
    db.upsert_article(article_row)

    if sleep:
        time.sleep(sleep)
    return row["word_count"], new_wc, engine


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--max-words", type=int, default=400,
                    help="re-extract articles with stored word_count below this "
                         "(default: 400)")
    ap.add_argument("--ids", type=int, nargs="+",
                    help="only re-extract these article ids")
    ap.add_argument("--all", action="store_true",
                    help="re-extract every article")
    ap.add_argument("--apply", action="store_true",
                    help="actually write changes to the DB (otherwise dry-run)")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="seconds to sleep between articles (default 1.0)")
    args = ap.parse_args(argv)

    db.init_db()
    rows = _fetch_candidates(ids=args.ids, max_words=args.max_words,
                             all_rows=args.all)
    if not rows:
        print("Nothing to do.")
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] rescraping {len(rows)} articles "
          f"(threshold={args.max_words} words)")
    print("-" * 78)

    improved = 0
    regressed_or_same = 0
    failed = 0
    total_gain = 0

    for row in rows:
        try:
            old_wc, new_wc, engine = _rewrite_one(row, apply=args.apply, sleep=args.sleep)
        except Exception as exc:  # keep going through the batch
            print(f"  id={row['id']:<4} ERROR  {exc}")
            failed += 1
            continue

        delta = new_wc - old_wc
        marker = "+" if delta > 0 else ("=" if delta == 0 else "-")
        print(f"  id={row['id']:<4} {old_wc:>4} → {new_wc:<4} {marker}{abs(delta):<4} "
              f"[{engine:<22}] {row['title'][:60]}")
        if engine == "failed":
            failed += 1
        elif new_wc > old_wc:
            improved += 1
            total_gain += delta
        else:
            regressed_or_same += 1

    print("-" * 78)
    print(f"improved: {improved}   unchanged: {regressed_or_same}   failed: {failed}")
    if improved:
        print(f"total words recovered: {total_gain}")
    if not args.apply:
        print("\n(dry-run — re-run with --apply to persist changes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
