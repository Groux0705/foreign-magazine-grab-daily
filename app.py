"""Flask web server for the offline reading & learning platform.

Pages:
  /                     -> today's feed
  /library              -> my saved / favorite / read articles
  /articles/<id>        -> reader view (fully offline)
  /vocabulary           -> vocabulary list

JSON API:
  GET  /api/dates
  GET  /api/categories
  GET  /api/feed?date=YYYY-MM-DD
  GET  /api/library?filter=&category=&q=
  GET  /api/articles/<id>
  POST /api/articles/<id>/favorite   body: {"value": bool}
  POST /api/articles/<id>/library    body: {"value": bool}
  POST /api/articles/<id>/read       body: {"value": bool}

  GET    /api/articles/<id>/annotations
  POST   /api/articles/<id>/annotations
  PATCH  /api/annotations/<id>
  DELETE /api/annotations/<id>

  GET    /api/vocabulary?q=&mastery=&sort=
  POST   /api/vocabulary
  PATCH  /api/vocabulary/<id>
  DELETE /api/vocabulary/<id>
  GET    /api/vocabulary/export.csv

  POST /api/refresh     -> trigger a scrape
"""
from __future__ import annotations

import csv
import io
import os
import threading
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (Flask, Response, abort, jsonify, render_template, request,
                   send_from_directory)
import requests

import annotations as anno_helpers
import db
import scraper

ROOT = Path(__file__).resolve().parent
ASSETS_DIR = ROOT / "data" / "assets"

app = Flask(__name__, static_folder="static", template_folder="templates")

_scrape_lock = threading.Lock()


def _safe_scrape(date_str: str | None = None):
    if not _scrape_lock.acquire(blocking=False):
        scraper.log.info("Scrape already in progress, skipping.")
        return None
    try:
        return scraper.run(date_str)
    finally:
        _scrape_lock.release()


def _categories_dict() -> dict:
    return {
        key: {"label": v["label"], "label_cn": v["label_cn"]}
        for key, v in scraper.CATEGORIES.items()
    }


def _category_order() -> list[str]:
    return list(scraper.CATEGORIES.keys())


def _strip_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return bool(value)


# -------------------- pages --------------------

@app.route("/")
def page_index():
    return render_template("index.html")


@app.route("/library")
def page_library():
    return render_template("library.html")


@app.route("/articles/<int:article_id>")
def page_reader(article_id: int):
    article = db.get_article(article_id)
    if not article:
        abort(404)
    return render_template("reader.html", article=article, categories=_categories_dict())


@app.route("/vocabulary")
def page_vocabulary():
    return render_template("vocabulary.html")


# -------------------- asset server --------------------

@app.route("/static/assets/<int:article_id>/<path:filename>")
def serve_asset(article_id: int, filename: str):
    return send_from_directory(ASSETS_DIR / str(article_id), filename)


# -------------------- metadata --------------------

@app.route("/api/dates")
def api_dates():
    return jsonify({"dates": db.distinct_fetch_dates()})


@app.route("/api/categories")
def api_categories():
    return jsonify(_categories_dict())


# -------------------- feed / library --------------------

def _serialize_article(row: dict, include_content: bool = False) -> dict:
    cat = row["category"]
    label = scraper.CATEGORIES.get(cat, {}).get("label", cat.title())
    label_cn = scraper.CATEGORIES.get(cat, {}).get("label_cn", cat)
    data = {
        "id": row["id"],
        "category": cat,
        "category_label": label,
        "category_label_cn": label_cn,
        "title": row["title"],
        "url": row["source_url"],
        "description": row.get("description"),
        "author": row.get("author"),
        "published_at": row.get("published_at"),
        "fetched_at": row.get("fetched_at"),
        "fetch_date": row.get("fetch_date"),
        "cover_image": row.get("cover_image"),
        "word_count": row.get("word_count", 0),
        "is_favorite": bool(row.get("is_favorite")),
        "in_library": bool(row.get("in_library")),
        "is_read": bool(row.get("is_read")),
        "read_at": row.get("read_at"),
    }
    if include_content:
        data["content_html"] = row.get("content_html") or ""
        data["plain_text"] = row.get("plain_text") or ""
    return data


@app.route("/api/feed")
def api_feed():
    dates = db.distinct_fetch_dates()
    date = request.args.get("date") or (dates[0] if dates else None)
    articles_by_cat = {k: [] for k in _category_order()}
    if date:
        for row in db.list_articles(date=date, limit=100):
            cat = row["category"]
            articles_by_cat.setdefault(cat, []).append(_serialize_article(row))
    return jsonify({
        "date": date,
        "available_dates": dates,
        "categories": _categories_dict(),
        "category_order": _category_order(),
        "articles": articles_by_cat,
    })


@app.route("/api/library")
def api_library():
    flt = request.args.get("filter", "all")
    category = request.args.get("category") or None
    query = request.args.get("q") or None
    kwargs: dict = {"category": category, "query": query, "limit": 500}
    if flt == "favorite":
        kwargs["favorite"] = True
    elif flt == "library":
        kwargs["in_library"] = True
    elif flt == "read":
        kwargs["is_read"] = True
    elif flt == "unread":
        kwargs["is_read"] = False
    rows = db.list_articles(**kwargs)
    return jsonify({
        "articles": [_serialize_article(r) for r in rows],
        "categories": _categories_dict(),
        "count": len(rows),
        "filter": flt,
    })


# -------------------- article detail / flags --------------------

@app.route("/api/articles/<int:article_id>")
def api_article_detail(article_id: int):
    row = db.get_article(article_id)
    if not row:
        abort(404)
    return jsonify(_serialize_article(row, include_content=True))


@app.route("/api/articles/<int:article_id>/<flag>", methods=["POST"])
def api_article_flag(article_id: int, flag: str):
    mapping = {"favorite": "is_favorite", "library": "in_library", "read": "is_read"}
    if flag not in mapping:
        abort(404)
    body = request.get_json(silent=True) or {}
    # If no explicit value provided, treat as toggle against current state.
    row = db.get_article(article_id)
    if not row:
        abort(404)
    if "value" in body:
        value = _strip_flag(body["value"])
    else:
        value = not bool(row[mapping[flag]])
    updated = db.set_article_flag(article_id, mapping[flag], value)
    return jsonify(_serialize_article(updated))


# -------------------- annotations --------------------

@app.route("/api/articles/<int:article_id>/annotations", methods=["GET", "POST"])
def api_article_annotations(article_id: int):
    row = db.get_article(article_id)
    if not row:
        abort(404)
    if request.method == "GET":
        return jsonify({"annotations": db.list_annotations(article_id)})

    body = request.get_json(force=True, silent=True) or {}
    required = ("quote", "start_xpath", "end_xpath")
    if not all(k in body for k in required):
        return jsonify({"error": "missing fields", "required": list(required)}), 400
    payload = {
        "article_id": article_id,
        "kind": body.get("kind", "highlight"),
        "color": body.get("color"),
        "quote": body.get("quote", ""),
        "prefix": body.get("prefix"),
        "suffix": body.get("suffix"),
        "start_xpath": body.get("start_xpath"),
        "start_offset": int(body.get("start_offset", 0) or 0),
        "end_xpath": body.get("end_xpath"),
        "end_offset": int(body.get("end_offset", 0) or 0),
        "comment": body.get("comment"),
    }
    created = db.create_annotation(payload)
    return jsonify(created), 201


@app.route("/api/annotations/<int:anno_id>", methods=["PATCH", "DELETE"])
def api_annotation_edit(anno_id: int):
    if request.method == "DELETE":
        db.delete_annotation(anno_id)
        return jsonify({"ok": True})
    body = request.get_json(force=True, silent=True) or {}
    updated = db.update_annotation(anno_id, body)
    if not updated:
        return jsonify({"error": "no allowed fields"}), 400
    return jsonify(updated)


# -------------------- vocabulary --------------------

@app.route("/api/vocabulary", methods=["GET", "POST"])
def api_vocabulary():
    if request.method == "GET":
        mastery = request.args.get("mastery")
        mastery_val = int(mastery) if mastery and mastery.isdigit() else None
        return jsonify({
            "words": db.list_vocabulary(
                query=request.args.get("q") or None,
                mastery=mastery_val,
                sort=request.args.get("sort", "recent"),
            )
        })

    body = request.get_json(force=True, silent=True) or {}
    word = (body.get("word") or "").strip()
    if not word:
        return jsonify({"error": "word is required"}), 400
    lemma = anno_helpers.normalize_phrase(word) or anno_helpers.lemmatize(word)
    if not lemma:
        return jsonify({"error": "cannot normalize word"}), 400
    created = db.upsert_vocabulary(
        word=word,
        lemma=lemma,
        context=body.get("context"),
        article_id=body.get("article_id"),
        note=body.get("note"),
    )
    return jsonify(created), 201


@app.route("/api/vocabulary/<int:vid>", methods=["PATCH", "DELETE"])
def api_vocabulary_edit(vid: int):
    if request.method == "DELETE":
        db.delete_vocabulary(vid)
        return jsonify({"ok": True})
    body = request.get_json(force=True, silent=True) or {}
    if "mastery" in body:
        try:
            body["mastery"] = max(0, min(3, int(body["mastery"])))
        except (TypeError, ValueError):
            body.pop("mastery")
    updated = db.update_vocabulary(vid, body)
    if not updated:
        return jsonify({"error": "no allowed fields"}), 400
    return jsonify(updated)


# -------------------- MaiMemo integration --------------------

MAIMEMO_BASE = "https://open.maimemo.com/open/api/v1"


def _maimemo_token() -> str:
    # Keep name explicit; user can export either key.
    return (os.environ.get("MAIMEMO_TOKEN") or os.environ.get("MOMO_API_KEY") or "").strip()


def _maimemo_headers() -> dict:
    tok = _maimemo_token()
    if not tok:
        return {}
    return {"Accept": "application/json", "Authorization": f"Bearer {tok}"}


def _pick_vocab_id(payload: dict, query: str) -> str | None:
    """Best-effort extraction of MaiMemo vocab id from lookup response."""
    if not isinstance(payload, dict):
        return None

    # common shapes
    direct = payload.get("id") or payload.get("voc_id")
    if isinstance(direct, str) and direct:
        return direct

    for key in ("vocabulary", "word", "data"):
        obj = payload.get(key)
        if isinstance(obj, dict):
            vid = obj.get("id") or obj.get("voc_id")
            if isinstance(vid, str) and vid:
                return vid

    # list shapes
    for key in ("vocabularies", "words", "items", "list", "data"):
        arr = payload.get(key)
        if isinstance(arr, list) and arr:
            q = (query or "").strip().lower()
            # prefer exact match if possible
            for it in arr:
                if not isinstance(it, dict):
                    continue
                txt = (it.get("word") or it.get("text") or it.get("name") or "").strip().lower()
                if q and txt == q:
                    vid = it.get("id") or it.get("voc_id")
                    if isinstance(vid, str) and vid:
                        return vid
            # else first item with id
            for it in arr:
                if not isinstance(it, dict):
                    continue
                vid = it.get("id") or it.get("voc_id")
                if isinstance(vid, str) and vid:
                    return vid

    return None


@app.route("/api/maimemo/add_word", methods=["POST"])
def api_maimemo_add_word():
    tok = _maimemo_token()
    if not tok:
        return jsonify({
            "error": "missing_token",
            "message": "未配置墨墨 Token。请设置环境变量 MAIMEMO_TOKEN（或 MOMO_API_KEY）。",
        }), 400

    body = request.get_json(force=True, silent=True) or {}
    raw = (body.get("word") or "").strip()
    if not raw:
        return jsonify({"error": "word is required"}), 400

    # For multi-word phrases, normalize; for single word, lemmatize.
    phrase = raw.strip()
    normalized = anno_helpers.normalize_phrase(phrase) or anno_helpers.lemmatize(phrase) or phrase

    try:
        # 1) lookup vocabulary id by text
        lookup = requests.get(
            f"{MAIMEMO_BASE}/vocabulary",
            params={"q": normalized},
            headers=_maimemo_headers(),
            timeout=12,
        )
        if not lookup.ok:
            return jsonify({
                "error": "lookup_failed",
                "status": lookup.status_code,
                "detail": lookup.text[:800],
            }), 502
        payload = lookup.json()
        voc_id = _pick_vocab_id(payload, normalized)
        if not voc_id:
            return jsonify({
                "error": "not_found",
                "message": f"墨墨未找到该词条：{normalized}",
            }), 404

        # 2) add to study
        resp = requests.post(
            f"{MAIMEMO_BASE}/study/add_words",
            json={"words": [{"id": voc_id}], "advance": False},
            headers={**_maimemo_headers(), "Content-Type": "application/json"},
            timeout=12,
        )
        if not resp.ok:
            return jsonify({
                "error": "add_failed",
                "status": resp.status_code,
                "detail": resp.text[:800],
            }), 502
        out = resp.json() if resp.content else {}
        return jsonify({
            "ok": True,
            "word": raw,
            "normalized": normalized,
            "vocab_id": voc_id,
            "added_count": out.get("added_count"),
            "raw_response": out,
        })
    except requests.RequestException as e:
        return jsonify({"error": "network_error", "message": str(e)}), 502


@app.route("/api/vocabulary/export.csv")
def api_vocabulary_export():
    rows = db.iter_vocabulary()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["word", "lemma", "mastery", "note", "context", "article", "created_at"])
    for r in rows:
        writer.writerow([
            r.get("word"), r.get("lemma"), r.get("mastery"),
            r.get("note") or "", (r.get("context") or "").replace("\n", " | "),
            r.get("article_title") or "", r.get("created_at"),
        ])
    csv_bytes = buf.getvalue().encode("utf-8-sig")
    filename = f"vocabulary-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# -------------------- refresh / scheduler --------------------

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    record = _safe_scrape()
    if record is None:
        return jsonify({"status": "busy", "message": "抓取任务正在运行中，请稍候"}), 202
    return jsonify({"status": "ok", "date": record["date"], "count": record["count"]})


def _start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(
        _safe_scrape,
        trigger="cron",
        hour=8,
        minute=0,
        id="daily_time_scrape",
        replace_existing=True,
    )
    sched.start()
    scraper.log.info("Scheduler started — daily scrape at 08:00 Asia/Shanghai")
    return sched


def _bootstrap_initial_scrape() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    if today in db.distinct_fetch_dates():
        return
    threading.Thread(target=_safe_scrape, daemon=True).start()


if __name__ == "__main__":
    db.init_db()
    _start_scheduler()
    _bootstrap_initial_scrape()
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=False)
