"""
SiteScope Dashboard
===================
Receives tracking events from JS and Flask-based sites,
stores them in SQLite, and serves a live analytics dashboard.

Run:
    pip install flask
    python app.py
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, Response, g, jsonify, render_template, request

app = Flask(__name__)
DB_PATH = Path(__file__).with_name("analytics.db")


# ──────────────────────────────────────────────────────────────
# CORS
# ──────────────────────────────────────────────────────────────

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


# ──────────────────────────────────────────────────────────────
# Database helpers
# ──────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          TEXT PRIMARY KEY,
            app_id      TEXT NOT NULL,
            event       TEXT NOT NULL,
            session_id  TEXT,
            visitor_id  TEXT,
            url         TEXT,
            path        TEXT,
            title       TEXT,
            referrer    TEXT,
            source      TEXT,
            utm         TEXT,
            user_agent  TEXT,
            language    TEXT,
            screen_w    INTEGER,
            screen_h    INTEGER,
            ip          TEXT,
            duration_ms INTEGER,
            ts          TEXT NOT NULL
        )
    """)
    # Sentinel table: one row written once so seed_demo knows the DB has been
    # initialised even after the user resets all event data.
    db.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_app_ts  ON events(app_id, ts)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_visitor ON events(visitor_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_session ON events(session_id)")
    db.commit()
    db.close()


# ──────────────────────────────────────────────────────────────
# Collection endpoint
# ──────────────────────────────────────────────────────────────

@app.route("/collect", methods=["POST", "OPTIONS"])
def collect():
    if request.method == "OPTIONS":
        return "", 204

    try:
        ct = request.content_type or ""
        if "application/json" in ct:
            data = request.get_json(silent=True) or {}
        else:
            data = json.loads(request.get_data(as_text=True) or "{}") or {}
    except Exception:
        return jsonify(ok=False, error="bad json"), 400

    if not data.get("app_id"):
        return jsonify(ok=False, error="missing app_id"), 400

    utm = data.get("utm") or {}
    if isinstance(utm, dict):
        utm = json.dumps(utm)

    db = get_db()
    db.execute(
        """
        INSERT INTO events
          (id, app_id, event, session_id, visitor_id, url, path, title,
           referrer, source, utm, user_agent, language,
           screen_w, screen_h, ip, duration_ms, ts)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            str(uuid.uuid4()),
            data.get("app_id", ""),
            data.get("event", "pageview"),
            data.get("session_id"),
            data.get("visitor_id"),
            data.get("url", ""),
            data.get("path", "/"),
            data.get("title", ""),
            data.get("referrer", ""),
            data.get("source", "direct"),
            utm,
            data.get("user_agent", request.user_agent.string),
            data.get("language", ""),
            data.get("screen_w"),
            data.get("screen_h"),
            data.get("ip", request.remote_addr),
            data.get("duration_ms"),
            data.get("ts", datetime.now(timezone.utc).isoformat()),
        ),
    )
    db.commit()
    return jsonify(ok=True), 201


# ──────────────────────────────────────────────────────────────
# Analytics query helpers
# ──────────────────────────────────────────────────────────────

def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _q(db, sql, params=()):
    return db.execute(sql, params).fetchall()


def app_ids(db) -> list[str]:
    rows = _q(db, "SELECT DISTINCT app_id FROM events ORDER BY app_id")
    return [r["app_id"] for r in rows]


def overview(db, aid: str, days: int = 30) -> dict:
    since = _since(days)
    pv       = _q(db, "SELECT COUNT(*) c FROM events WHERE app_id=? AND event='pageview' AND ts>=?", (aid, since))[0]["c"]
    visitors = _q(db, "SELECT COUNT(DISTINCT visitor_id) c FROM events WHERE app_id=? AND ts>=?", (aid, since))[0]["c"]
    sessions = _q(db, "SELECT COUNT(DISTINCT session_id) c FROM events WHERE app_id=? AND ts>=?", (aid, since))[0]["c"]
    dur      = _q(db, "SELECT AVG(duration_ms) avg_ms FROM events WHERE app_id=? AND event='session_end' AND duration_ms IS NOT NULL AND ts>=?", (aid, since))[0]["avg_ms"]
    return {"pageviews": pv, "visitors": visitors, "sessions": sessions, "avg_duration_s": round((dur or 0) / 1000, 1)}


def top_pages(db, aid: str, days: int = 30, limit: int = 10) -> list[dict]:
    rows = _q(db, "SELECT path, COUNT(*) views FROM events WHERE app_id=? AND event='pageview' AND ts>=? GROUP BY path ORDER BY views DESC LIMIT ?", (aid, _since(days), limit))
    return [dict(r) for r in rows]


def top_sources(db, aid: str, days: int = 30, limit: int = 10) -> list[dict]:
    rows = _q(db, "SELECT source, COUNT(*) visits FROM events WHERE app_id=? AND event='pageview' AND ts>=? GROUP BY source ORDER BY visits DESC LIMIT ?", (aid, _since(days), limit))
    return [dict(r) for r in rows]


def pageviews_over_time(db, aid: str, days: int = 30) -> list[dict]:
    rows = _q(db, "SELECT substr(ts,1,10) day, COUNT(*) views FROM events WHERE app_id=? AND event='pageview' AND ts>=? GROUP BY day ORDER BY day", (aid, _since(days)))
    return [dict(r) for r in rows]


def top_languages(db, aid: str, days: int = 30) -> list[dict]:
    rows = _q(db, "SELECT COALESCE(NULLIF(language,''), 'unknown') lang, COUNT(*) cnt FROM events WHERE app_id=? AND event='pageview' AND ts>=? GROUP BY lang ORDER BY cnt DESC LIMIT 8", (aid, _since(days)))
    return [dict(r) for r in rows]


def recent_events(db, aid: str, limit: int = 30) -> list[dict]:
    rows = _q(db, "SELECT event, path, source, ts FROM events WHERE app_id=? ORDER BY ts DESC LIMIT ?", (aid, limit))
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    db = get_db()
    apps = app_ids(db)
    return render_template("index.html", apps=apps)


@app.route("/app/<app_id>")
def app_detail(app_id: str):
    days = int(request.args.get("days", 30))
    db = get_db()
    return render_template(
        "app_detail.html",
        app_id=app_id, days=days,
        overview=overview(db, app_id, days),
        top_pages=top_pages(db, app_id, days),
        top_sources=top_sources(db, app_id, days),
        chart_data=pageviews_over_time(db, app_id, days),
        languages=top_languages(db, app_id, days),
        recent=recent_events(db, app_id),
        apps=app_ids(db),
    )


@app.route("/app/<app_id>/stats")
def app_stats(app_id: str):
    days = int(request.args.get("days", 30))
    db = get_db()
    return render_template("partials/stats_cards.html", app_id=app_id, overview=overview(db, app_id, days))


@app.route("/app/<app_id>/recent")
def app_recent(app_id: str):
    db = get_db()
    return render_template("partials/recent_events.html", app_id=app_id, recent=recent_events(db, app_id))


@app.route("/sidebar")
def sidebar_partial():
    db = get_db()
    return render_template("partials/sidebar_apps.html", apps=app_ids(db))


@app.route("/app/<app_id>/delete", methods=["POST"])
def delete_app(app_id: str):
    db = get_db()
    db.execute("DELETE FROM events WHERE app_id = ?", (app_id,))
    db.commit()
    # HX-Redirect causes htmx.ajax() to do a full client navigation.
    # The referrer header tells us which page the user is currently on so we
    # can send them home only when they were viewing the deleted app's page,
    # and keep them on their current page otherwise.
    ref = request.referrer or ""
    if f"/app/{app_id}" in ref:
        redirect_to = "/"
    else:
        redirect_to = ref or "/"
    r = Response("", status=200)
    r.headers["HX-Redirect"] = redirect_to
    return r


@app.route("/db/reset", methods=["POST"])
def reset_db():
    db = get_db()
    db.execute("DELETE FROM events")
    db.commit()
    r = Response("", status=200)
    r.headers["HX-Redirect"] = "/"
    return r


# ──────────────────────────────────────────────────────────────
# Seed demo data
# ──────────────────────────────────────────────────────────────

def seed_demo() -> None:
    """
    Seeds demo events only on the very first run.
    Uses the `meta` table as a sentinel so that a user reset (which only
    clears `events`) does NOT cause the demo data to reappear on restart.
    """
    import random
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    already = db.execute("SELECT value FROM meta WHERE key='seeded'").fetchone()
    if already:
        db.close()
        return

    demo_apps = ["portfolio-site", "shop-frontend", "api-docs"]
    paths     = ["/", "/about", "/blog", "/contact", "/pricing", "/docs/quickstart", "/docs/api"]
    sources   = ["direct", "search:Google", "social:Twitter", "referral:github.com", "search:Bing"]
    now = datetime.now(timezone.utc)

    rows = []
    for aid in demo_apps:
        for _ in range(random.randint(120, 350)):
            days_ago = random.uniform(0, 30)
            ts = (now - timedelta(days=days_ago)).isoformat()
            rows.append((
                str(uuid.uuid4()), aid, "pageview",
                str(uuid.uuid4()), str(uuid.uuid4()),
                "https://example.com" + random.choice(paths),
                random.choice(paths), "My Site",
                "", random.choice(sources), "{}",
                "Mozilla/5.0", "en-US",
                random.choice([1280, 1920, 390, 768]),
                random.choice([720, 1080, 844, 1024]),
                "127.0.0.1", None, ts,
            ))

    db.executemany("""
        INSERT INTO events (id,app_id,event,session_id,visitor_id,url,path,title,
          referrer,source,utm,user_agent,language,screen_w,screen_h,ip,duration_ms,ts)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    # Write the sentinel — persists across resets since reset only touches events
    db.execute("INSERT INTO meta(key,value) VALUES('seeded','1')")
    db.commit()
    db.close()
    print(f"[SiteScope] Seeded {len(rows)} demo events across {len(demo_apps)} apps.")


if __name__ == "__main__":
    init_db()
    seed_demo()
    app.run(debug=True, port=5000)