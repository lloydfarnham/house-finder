"""Run every search, filter to our criteria, merge duplicates, notify, and write the data files.

Usage:  python -m scraper.main            (respects the 8am-7pm window)
        FORCE=1 python -m scraper.main    (run regardless of time)
        DEBUG=1 ...                       (save raw pages to ./debug for inspection)
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import notify
from .sources import onthemarket, openrent, rightmove, zoopla
from .util import Fetcher, cycle_minutes, haversine_m, now_iso, parse_available, walk_minutes

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
STATE = ROOT / "data" / "state.json"
OUTPUT = ROOT / "docs" / "data" / "listings.json"

SOURCES = {
    "rightmove": rightmove,
    "zoopla": zoopla,
    "onthemarket": onthemarket,
    "openrent": openrent,
}
SOURCE_LABEL = {"rightmove": "Rightmove", "zoopla": "Zoopla", "onthemarket": "OnTheMarket", "openrent": "OpenRent"}
SOURCE_PRIORITY = ["rightmove", "zoopla", "onthemarket", "openrent"]
DETAIL_BUDGET = int(os.environ.get("DETAIL_BUDGET", "70"))
CARRY_OVER_HOURS = 36


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def in_window(cfg) -> bool:
    sch = cfg["schedule"]
    now = datetime.now(ZoneInfo(sch["timezone"]))
    return sch["firstHour"] <= now.hour <= sch["lastHour"]


# ----------------------------------------------------------------- filtering helpers

FEATURE_TAGS = [
    ("Garden", r"\b(private garden|rear garden|own garden|garden)\b"),
    ("Balcony", r"\bbalcon(y|ies)\b"),
    ("Terrace", r"\b(roof terrace|terrace)\b(?! house)"),
    ("Patio", r"\bpatio\b"),
    ("Bills incl.", r"\bbills included|inclusive of bills|all bills\b"),
    ("Pets OK", r"\bpets? (considered|allowed|welcome|friendly)\b"),
    ("Furnished", r"(?<!un)(?<!part )\bfurnished\b"),
    ("Unfurnished", r"\bunfurnished\b"),
    ("Separate lounge", r"\bseparate (living|lounge|reception)|\breception room\b"),
]


def blob_text(l: dict) -> str:
    parts = [l.get("title"), l.get("summary"), l.get("description"), l.get("address"), " ".join(l.get("features") or [])]
    return " ".join(p for p in parts if p).lower()


def nearest(stations, lat, lon):
    best = None
    for st in stations:
        d = haversine_m(lat, lon, st["lat"], st["lon"])
        if best is None or d < best[1]:
            best = (st, d)
    return best


def postcode_district(address: str) -> str | None:
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)(?:\s*\d[A-Z]{2})?\b\s*$", address.strip().upper())
    return m.group(1) if m else None


def prefilter(l: dict, cfg: dict) -> str | None:
    """Return a reason string if the listing should be dropped, else None."""
    s = cfg["search"]
    if l.get("letAgreed"):
        return "let agreed"
    if l.get("pricePcm") is None or l["pricePcm"] > s["maxPricePcm"] or l["pricePcm"] < 500:
        return "price"
    if l.get("beds") is not None and not (s["minBeds"] <= l["beds"] <= s["maxBeds"]):
        return "bedrooms"
    text = blob_text(l)
    for kw in cfg["excludeKeywords"]:
        if kw in text:
            return f"keyword:{kw}"
    addr = (l.get("address") or "").lower()
    for w in cfg["excludeAddressWords"]:
        if w in addr:
            return "excluded area"
    pc = postcode_district(l.get("address") or "")
    if pc and pc in cfg["excludePostcodeDistricts"]:
        return "excluded area"
    if l.get("lat") and l.get("lon"):
        try:
            lat, lon = float(l["lat"]), float(l["lon"])
        except (TypeError, ValueError):
            return None
        st, d = nearest(cfg["stations"], lat, lon)
        ex = nearest(cfg["excludeNearStations"], lat, lon) if cfg["excludeNearStations"] else None
        if ex and ex[1] < d:
            return "excluded area"
        if walk_minutes(d) > s["maxWalkMins"]:
            return "too far from tube"
    return None


def enrich(l: dict, cfg: dict) -> None:
    s = cfg["search"]
    if l.get("lat") and l.get("lon"):
        lat, lon = float(l["lat"]), float(l["lon"])
        st, d = nearest(cfg["stations"], lat, lon)
        c = cfg["commute"]
        l["station"] = {"name": st["name"], "lines": st["lines"], "walkMins": walk_minutes(d), "distanceM": round(d)}
        l["area"], l["tier"] = st["area"], st["tier"]
        l["cycleMins"] = cycle_minutes(haversine_m(lat, lon, c["lat"], c["lon"]))
    else:
        st = next(x for x in cfg["stations"] if x["name"] == l["searchedStation"])
        l["station"] = {"name": st["name"], "lines": st["lines"], "walkMins": None, "distanceM": None}
        l["area"], l["tier"] = st["area"], st["tier"]
        l["cycleMins"] = None
    # availability
    earliest = date.fromisoformat(s["moveInEarliest"])
    latest = date.fromisoformat(s["moveInLatest"])
    parsed = parse_available(l.get("availableText"))
    if parsed is None:
        l["availability"] = {"kind": "unknown", "date": None, "flag": "unknown", "text": l.get("availableText")}
    elif parsed[0] == "now":
        l["availability"] = {"kind": "now", "date": None, "flag": "early", "text": "Available now"}
    else:
        d = parsed[1]
        flag = "ok" if earliest - timedelta(days=21) <= d <= latest else ("late" if d > latest else "early")
        l["availability"] = {"kind": "date", "date": d.isoformat(), "flag": flag,
                             "text": f"Available {d.strftime('%-d %b %Y')}"}
    text = blob_text(l)
    l["tags"] = [name for name, pat in FEATURE_TAGS if re.search(pat, text)]
    if "Unfurnished" in l["tags"] and "Furnished" in l["tags"]:
        l["tags"].remove("Furnished")
    if l.get("furnished") and isinstance(l["furnished"], str):
        f = l["furnished"].lower()
        l["tags"] = [t for t in l["tags"] if t not in ("Furnished", "Unfurnished")]
        l["tags"].append("Unfurnished" if "unfurn" in f else ("Part furnished" if "part" in f else "Furnished"))


def postfilter(l: dict, cfg: dict) -> str | None:
    if l.get("gone"):
        return "no longer listed"
    if l["availability"]["flag"] == "late":
        return "available after January"
    text = blob_text(l)
    for kw in cfg["noLivingSpaceKeywords"]:
        if kw in text:
            return "no living space"
    return None


# ----------------------------------------------------------------- de-duplication

def same_property(a: dict, b: dict) -> bool:
    if a["source"] == b["source"]:
        return False
    if a.get("beds") != b.get("beds"):
        return False
    if abs((a.get("pricePcm") or 0) - (b.get("pricePcm") or 0)) > 30:
        return False
    if a.get("lat") and b.get("lat"):
        return haversine_m(float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"])) < 90
    na = re.sub(r"[^a-z0-9]", "", (a.get("address") or "").lower())[:25]
    nb = re.sub(r"[^a-z0-9]", "", (b.get("address") or "").lower())[:25]
    return bool(na) and na == nb


def cluster(listings: list[dict]) -> list[list[dict]]:
    listings = sorted(listings, key=lambda l: SOURCE_PRIORITY.index(l["source"]))
    groups: list[list[dict]] = []
    for l in listings:
        for g in groups:
            if any(same_property(l, m) for m in g) and l["source"] not in {m["source"] for m in g}:
                g.append(l)
                break
        else:
            groups.append([l])
    return groups


def merge_group(members: list[dict], state: dict) -> dict:
    aliases = state.setdefault("aliases", {})
    gid = next((aliases[m["key"]] for m in members if m["key"] in aliases), members[0]["key"])
    for m in members:
        aliases[m["key"]] = gid
    base = dict(members[0])
    for m in members[1:]:
        for k, v in m.items():
            if base.get(k) in (None, "", [], {}) and v not in (None, "", [], {}):
                base[k] = v
        # prefer a known availability date over unknown
        if base["availability"]["kind"] == "unknown" and m["availability"]["kind"] != "unknown":
            base["availability"] = m["availability"]
    base["id"] = gid
    base["price"] = min(m["pricePcm"] for m in members)
    base["links"] = [{"source": m["source"], "label": SOURCE_LABEL[m["source"]], "url": m["url"]} for m in members]
    base["tags"] = sorted({t for m in members for t in m.get("tags", [])})
    for k in ("searchedStation", "pricePcm", "key", "sourceId", "letAgreed", "gone", "description"):
        base.pop(k, None)
    desc = next((m.get("description") for m in members if m.get("description")), None)
    base["description"] = (desc or "")[:1500]
    return base


# ----------------------------------------------------------------- main

def main() -> int:
    cfg = load_json(CONFIG, None)
    if cfg is None:
        print("config.json missing or invalid")
        return 1
    force = os.environ.get("FORCE") == "1"
    if not force and not in_window(cfg):
        print("Outside the 8am-7pm UK window; skipping.")
        return 0

    state = load_json(STATE, {})
    first_run = not state.get("groups")
    now = datetime.now(timezone.utc)
    source_status: dict = {}
    raw: list[dict] = []
    cache = state.setdefault("lastResults", {})

    for name, mod in SOURCES.items():
        if not cfg["sources"].get(name):
            continue
        print(f"[{name}] searching...")
        f = Fetcher(name)
        try:
            found = mod.search(cfg, state, f)
            cache[name] = {"at": now_iso(), "items": found}
            source_status[name] = {"ok": True, "found": len(found), "at": now_iso()}
            raw.extend(found)
        except Exception as e:
            print(f"[{name}] FAILED: {e}")
            traceback.print_exc()
            prev = cache.get(name)
            stale = None
            if prev:
                age = now - datetime.fromisoformat(prev["at"].replace("Z", "+00:00"))
                if age < timedelta(hours=CARRY_OVER_HOURS):
                    raw.extend(prev["items"])
                    stale = prev["at"]
            source_status[name] = {"ok": False, "error": str(e)[:200], "at": now_iso(), "usingResultsFrom": stale}

    # pre-filter (cheap) before fetching any listing pages
    dropped: dict[str, int] = {}
    survivors = []
    for l in raw:
        why = prefilter(l, cfg)
        if why:
            dropped[why.split(":")[0]] = dropped.get(why.split(":")[0], 0) + 1
        else:
            survivors.append(l)

    # listing pages: availability dates, descriptions (cached per listing)
    details = state.setdefault("details", {})
    fetchers = {name: Fetcher(name, 1.0, 2.5) for name in SOURCES}
    budget = DETAIL_BUDGET
    blocked: set[str] = set()
    survivors.sort(key=lambda l: next((st["tier"] for st in cfg["stations"] if st["name"] == l["searchedStation"]), 3))
    for l in survivors:
        d = details.get(l["key"])
        fresh = d and (now - datetime.fromisoformat(d["_at"].replace("Z", "+00:00"))) < timedelta(days=4)
        if l["source"] == "rightmove" and l.get("availableText"):
            fresh = True  # search results already carry the availability date
        if not fresh and budget > 0 and l["source"] not in blocked:
            budget -= 1
            try:
                d = SOURCES[l["source"]].details(l, fetchers[l["source"]])
                d = {k: v for k, v in d.items() if v not in (None, "", [])}
                d["_at"] = now_iso()
                details[l["key"]] = d
            except Exception as e:
                print(f"  [{l['source']}] details failed for {l['key']}: {e}")
                if "403" in str(e) or "429" in str(e):
                    blocked.add(l["source"])
        if d:
            for k, v in d.items():
                if k == "_at":
                    continue
                if k == "availableText" or not l.get(k):
                    l[k] = v

    final = []
    for l in survivors:
        if prefilter(l, cfg):  # coordinates may have arrived from the details page
            dropped["location"] = dropped.get("location", 0) + 1
            continue
        enrich(l, cfg)
        why = postfilter(l, cfg)
        if why:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        final.append(l)

    merged = [merge_group(g, state) for g in cluster(final)]

    # history: first seen, price changes
    groups = state.setdefault("groups", {})
    new_items, drops = [], []
    for m in merged:
        g = groups.get(m["id"])
        if g is None:
            g = groups[m["id"]] = {"firstSeen": now_iso(), "priceHistory": [[now_iso(), m["price"]]]}
            new_items.append(m)
        else:
            last_price = g["priceHistory"][-1][1]
            if m["price"] != last_price:
                g["priceHistory"].append([now_iso(), m["price"]])
                if m["price"] < last_price:
                    drops.append((m, last_price))
            g.pop("goneAt", None)
        g["lastSeen"] = now_iso()
        m["firstSeen"] = g["firstSeen"]
        m["priceHistory"] = g["priceHistory"][-6:]
        hist = g["priceHistory"]
        if len(hist) > 1 and hist[-1][1] < max(p for _, p in hist):
            m["reducedFrom"] = max(p for _, p in hist)
    live = {m["id"] for m in merged}
    for gid, g in groups.items():
        if gid not in live and "goneAt" not in g:
            g["goneAt"] = now_iso()
    # prune very old state
    cutoff = (now - timedelta(days=60)).isoformat()
    for gid in [k for k, g in groups.items() if g.get("goneAt", "9") < cutoff]:
        groups.pop(gid)
    for k in [k for k, d in details.items() if d.get("_at", "") < cutoff]:
        details.pop(k)

    merged.sort(key=lambda m: m["firstSeen"], reverse=True)
    out = {
        "generatedAt": now_iso(),
        "criteria": {
            **cfg["search"],
            "areas": sorted({st["area"] for st in cfg["stations"]}, key=lambda a: [st["area"] for st in cfg["stations"]].index(a)),
            "commute": cfg["commute"]["name"],
        },
        "sources": {SOURCE_LABEL[k]: v for k, v in source_status.items()},
        "excluded": dropped,
        "count": len(merged),
        "listings": merged,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    state["lastRun"] = now_iso()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\nDone: {len(raw)} raw, {len(final)} after filters, {len(merged)} unique. "
          f"New: {len(new_items)}, price drops: {len(drops)}. Dropped: {dropped}")
    print("Sources:", json.dumps(source_status))

    try:
        notify.send(cfg, new_items, drops, first_run=first_run, total=len(merged))
    except Exception as e:
        print("Notification failed:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
