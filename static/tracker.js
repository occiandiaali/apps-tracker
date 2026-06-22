/**
 * SiteScope Tracker — embed this in any JS-based website
 *
 * Usage:
 *   <script>
 *     window.SITESCAPE_CONFIG = {
 *       endpoint: "https://your-dashboard.com/collect",
 *       appId: "my-app-name"   // unique ID for this site
 *     };
 *   </script>
 *   <script src="tracker.js"></script>
 */
(function () {
  "use strict";

  const cfg = window.SITESCAPE_CONFIG || {};
  const ENDPOINT = cfg.endpoint || "http://localhost:5000/collect";
  const APP_ID = cfg.appId || "unknown-app";

  /* ── helpers ──────────────────────────────────────────────── */

  function getOrCreateSession() {
    const KEY = "ss_sid";
    let sid = sessionStorage.getItem(KEY);
    if (!sid) {
      sid = crypto.randomUUID
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2);
      sessionStorage.setItem(KEY, sid);
    }
    return sid;
  }

  function getOrCreateVisitor() {
    const KEY = "ss_vid";
    let vid = localStorage.getItem(KEY);
    if (!vid) {
      vid = crypto.randomUUID
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2);
      localStorage.setItem(KEY, vid);
    }
    return vid;
  }

  function parseReferrer(ref) {
    if (!ref) return { source: "direct", referrer: "" };
    try {
      const u = new URL(ref);
      const host = u.hostname.replace(/^www\./, "");
      const knownSearch = {
        "google.com": "Google",
        "bing.com": "Bing",
        "duckduckgo.com": "DuckDuckGo",
        "yahoo.com": "Yahoo",
      };
      const knownSocial = {
        "facebook.com": "Facebook",
        "twitter.com": "Twitter",
        "x.com": "X",
        "instagram.com": "Instagram",
        "linkedin.com": "LinkedIn",
        "reddit.com": "Reddit",
      };
      if (knownSearch[host])
        return { source: "search:" + knownSearch[host], referrer: ref };
      if (knownSocial[host])
        return { source: "social:" + knownSocial[host], referrer: ref };
      return { source: "referral:" + host, referrer: ref };
    } catch (_) {
      return { source: "unknown", referrer: ref };
    }
  }

  function utmParams() {
    const p = new URLSearchParams(location.search);
    const out = {};
    for (const k of [
      "utm_source",
      "utm_medium",
      "utm_campaign",
      "utm_term",
      "utm_content",
    ]) {
      if (p.has(k)) out[k] = p.get(k);
    }
    return out;
  }

  /* ── core send ────────────────────────────────────────────── */

  function send(eventName, extra) {
    const { source, referrer } = parseReferrer(document.referrer);
    const payload = {
      app_id: APP_ID,
      event: eventName,
      session_id: getOrCreateSession(),
      visitor_id: getOrCreateVisitor(),
      url: location.href,
      path: location.pathname,
      title: document.title,
      referrer,
      source,
      utm: utmParams(),
      screen_w: screen.width,
      screen_h: screen.height,
      language: navigator.language,
      ts: new Date().toISOString(),
      ...extra,
    };

    // prefer sendBeacon for reliability on unload, fall back to fetch
    const blob = new Blob([JSON.stringify(payload)], {
      type: "application/json",
    });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, blob);
    } else {
      fetch(ENDPOINT, {
        method: "POST",
        body: JSON.stringify(payload),
        headers: { "Content-Type": "application/json" },
        keepalive: true,
      }).catch(() => {});
    }
  }

  /* ── page-view ────────────────────────────────────────────── */

  function trackPageView() {
    send("pageview");
  }

  /* ── SPA support: intercept pushState / replaceState ─────── */

  const _push = history.pushState.bind(history);
  const _replace = history.replaceState.bind(history);

  history.pushState = function (...args) {
    _push(...args);
    setTimeout(trackPageView, 0);
  };
  history.replaceState = function (...args) {
    _replace(...args);
    setTimeout(trackPageView, 0);
  };
  window.addEventListener("popstate", () => setTimeout(trackPageView, 0));

  /* ── session duration on unload ──────────────────────────── */

  const sessionStart = Date.now();
  window.addEventListener("pagehide", () => {
    send("session_end", { duration_ms: Date.now() - sessionStart });
  });

  /* ── public API ───────────────────────────────────────────── */

  window.SiteScope = {
    /**
     * Track a custom event.
     * SiteScope.track("signup_click", { plan: "pro" });
     */
    track(name, props) {
      send(name, props || {});
    },
  };

  /* ── fire initial page-view ───────────────────────────────── */
  trackPageView();
})();
