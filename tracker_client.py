"""
SiteScope Tracker — Flask blueprint to embed in any Flask-based website
========================================================================

Installation
------------
1. Copy this file into your Flask project, e.g. as  tracker_client.py
2. Register the blueprint in your app factory / main file:

    from tracker_client import build_tracker_blueprint
    app.register_blueprint(
        build_tracker_blueprint(
            endpoint="https://your-dashboard.com/collect",
            app_id="my-flask-app",
        )
    )

3. Add the <script> snippet (injected automatically via the `tracker_js`
   template global) to your base template:

    {{ tracker_js() | safe }}

That's it — every request your Flask app serves is automatically tracked.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen, Request

import flask
from flask import Blueprint, g, make_response, request, session as flask_session


# ── referrer parsing ──────────────────────────────────────────────────────────

_SEARCH_ENGINES = {
    "google.com": "Google",
    "bing.com": "Bing",
    "duckduckgo.com": "DuckDuckGo",
    "yahoo.com": "Yahoo",
}
_SOCIAL_NETS = {
    "facebook.com": "Facebook",
    "twitter.com": "Twitter",
    "x.com": "X",
    "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn",
    "reddit.com": "Reddit",
}


def _parse_referrer(ref: str | None) -> tuple[str, str]:
    """Returns (source_label, referrer_url)."""
    if not ref:
        return "direct", ""
    try:
        host = urlparse(ref).hostname or ""
        host = host.removeprefix("www.")
        if host in _SEARCH_ENGINES:
            return f"search:{_SEARCH_ENGINES[host]}", ref
        if host in _SOCIAL_NETS:
            return f"social:{_SOCIAL_NETS[host]}", ref
        return f"referral:{host}", ref
    except Exception:
        return "unknown", ref or ""


# ── async fire-and-forget sender ──────────────────────────────────────────────

def _fire_and_forget(endpoint: str, payload: dict[str, Any]) -> None:
    """Send payload to the analytics endpoint in a daemon thread."""

    def _send() -> None:
        try:
            data = json.dumps(payload).encode()
            req = Request(
                endpoint,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urlopen(req, timeout=3)
        except Exception:
            pass  # never crash the host app

    t = threading.Thread(target=_send, daemon=True)
    t.start()


# ── cookie helpers ────────────────────────────────────────────────────────────

_VISITOR_COOKIE = "ss_vid"
_SESSION_COOKIE = "ss_sid"
_SESSION_TTL = 30 * 60  # 30 min inactivity = new session


def _get_or_create_visitor(response: flask.Response) -> str:
    vid = request.cookies.get(_VISITOR_COOKIE)
    if not vid:
        vid = str(uuid.uuid4())
        response.set_cookie(_VISITOR_COOKIE, vid, max_age=365 * 24 * 3600, samesite="Lax")
    return vid


def _get_or_create_session(response: flask.Response) -> str:
    sid = request.cookies.get(_SESSION_COOKIE)
    if not sid:
        sid = str(uuid.uuid4())
    response.set_cookie(_SESSION_COOKIE, sid, max_age=_SESSION_TTL, samesite="Lax")
    return sid


# ── blueprint factory ─────────────────────────────────────────────────────────

def build_tracker_blueprint(
    endpoint: str,
    app_id: str,
    excluded_paths: list[str] | None = None,
    blueprint_name: str = "sitescope_tracker",
) -> Blueprint:
    """
    Create and return a Flask Blueprint that auto-tracks every page view.

    Parameters
    ----------
    endpoint        URL of your SiteScope dashboard /collect route.
    app_id          Unique identifier for this website.
    excluded_paths  URL path prefixes that should NOT be tracked
                    (e.g. ["/static", "/health"]).
    blueprint_name  Avoid collisions if you register multiple trackers.
    """
    excluded_paths = excluded_paths or ["/static", "/favicon.ico"]
    bp = Blueprint(blueprint_name, __name__)

    @bp.after_app_request
    def _track(response: flask.Response) -> flask.Response:
        # skip non-HTML responses and excluded paths
        content_type = response.content_type or ""
        if "text/html" not in content_type:
            return response
        if any(request.path.startswith(p) for p in excluded_paths):
            return response
        # skip redirects — wait for the final destination
        if response.status_code in (301, 302, 303, 307, 308):
            return response

        source, referrer = _parse_referrer(request.referrer)

        # UTM params
        utm = {}
        for key in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"):
            val = request.args.get(key)
            if val:
                utm[key] = val

        vid = _get_or_create_visitor(response)
        sid = _get_or_create_session(response)

        payload: dict[str, Any] = {
            "app_id": app_id,
            "event": "pageview",
            "session_id": sid,
            "visitor_id": vid,
            "url": request.url,
            "path": request.path,
            "title": "",  # server-side; no DOM title
            "referrer": referrer,
            "source": source,
            "utm": utm,
            "screen_w": None,
            "screen_h": None,
            "language": (request.accept_languages.best or ""),
            "user_agent": request.user_agent.string,
            "ip": request.remote_addr,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        _fire_and_forget(endpoint, payload)
        return response

    # ── template helper injected into Jinja2 globals ──────────────────────────

    @bp.app_context_processor
    def _inject_tracker_js() -> dict[str, Any]:
        """
        Adds  tracker_js()  as a Jinja2 global so base templates can call:
            {{ tracker_js() | safe }}
        This embeds a tiny <script> that handles SPA navigations & custom events
        from the browser side (optional, but recommended for SPAs / JS-heavy pages).
        """

        def tracker_js() -> str:
            return f"""
<script>
  window.SITESCAPE_CONFIG = {{
    endpoint: {json.dumps(endpoint)},
    appId: {json.dumps(app_id)}
  }};
</script>
<script src="/static/tracker.js" defer></script>
""".strip()

        return {"tracker_js": tracker_js}

    return bp
