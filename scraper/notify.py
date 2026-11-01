"""Push notifications via ntfy.sh (free, no account: install the ntfy app and subscribe to the topic)."""
from __future__ import annotations

import os

from .util import http, _IMPERSONATE  # noqa: F401  (same HTTP stack)

MAX_INDIVIDUAL = 6


def _post(cfg: dict, payload: dict) -> None:
    n = cfg["notifications"]
    topic = os.environ.get("NTFY_TOPIC") or n.get("ntfyTopic")
    if not topic:
        return
    payload = {"topic": topic, **payload}
    r = http.post(n.get("ntfyServer", "https://ntfy.sh"), json=payload, timeout=20)
    if r.status_code >= 300:
        print(f"ntfy returned {r.status_code}: {r.text[:200]}")


def _line(m: dict) -> str:
    st = m.get("station") or {}
    walk = f"{st['walkMins']} min walk to {st['name']}" if st.get("walkMins") else f"near {st.get('name', m.get('area'))}"
    bits = [f"£{m['price']:,} pcm", f"{m.get('beds')} bed", walk]
    if m.get("cycleMins"):
        bits.append(f"~{m['cycleMins']} min cycle to Euston")
    av = (m.get("availability") or {}).get("text")
    if av:
        bits.append(av)
    if m.get("tags"):
        bits.append(", ".join(t for t in m["tags"] if t in ("Garden", "Balcony", "Terrace", "Patio")))
    return " · ".join(b for b in bits if b)


def send(cfg: dict, new_items: list, drops: list, *, first_run: bool, total: int) -> None:
    site = cfg["notifications"].get("siteUrl")
    if os.environ.get("NO_NOTIFY") == "1":
        return
    if first_run:
        if total:
            _post(cfg, {"title": f"House finder is live: {total} properties match",
                        "message": "You'll get a ping here whenever something new turns up or a price drops.",
                        "click": site, "tags": ["house"]})
        return
    # Don't ping about flats that will almost certainly be let before January
    fits = lambda m: (m.get("availability") or {}).get("flag") in ("ok", "unknown")
    new_items = [m for m in new_items if fits(m)]
    drops = [(m, old) for m, old in drops if fits(m)]
    # tier 1 areas first
    new_items = sorted(new_items, key=lambda m: (m.get("tier", 3), m["price"]))
    for m in new_items[:MAX_INDIVIDUAL]:
        payload = {
            "title": f"New: {m.get('title') or 'Property'} in {m.get('area')}",
            "message": f"{m.get('address')}\n{_line(m)}",
            "click": m["links"][0]["url"],
            "tags": ["house", "new"],
            "priority": 4 if m.get("tier") == 1 else 3,
            "actions": [{"action": "view", "label": "Open app", "url": site}],
        }
        if m.get("image"):
            payload["attach"] = m["image"]
        _post(cfg, payload)
    if len(new_items) > MAX_INDIVIDUAL:
        _post(cfg, {"title": f"+{len(new_items) - MAX_INDIVIDUAL} more new properties",
                    "message": "Open the app to see them all.", "click": site, "tags": ["house"]})
    for m, old in drops[:MAX_INDIVIDUAL]:
        _post(cfg, {
            "title": f"Price drop: £{old:,} → £{m['price']:,} pcm ({m.get('area')})",
            "message": f"{m.get('address')}\n{_line(m)}",
            "click": m["links"][0]["url"],
            "tags": ["chart_with_downwards_trend"],
        })


def test(cfg: dict, total: int) -> None:
    _post(cfg, {"title": "House Hunt notifications are working 🏡",
                "message": f"{total} properties currently match. You'll be pinged for new ones and price drops (8am-7pm).",
                "click": cfg["notifications"].get("siteUrl"), "tags": ["house", "white_check_mark"]})
