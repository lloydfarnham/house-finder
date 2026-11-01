"""OnTheMarket: area searches with a radius (results are geo-filtered afterwards)."""
from __future__ import annotations

import re
import urllib.parse as up

from ..util import Fetcher, haversine_m, find_available_in_text, find_key, html_to_text, json_blobs, meta_content, price_to_pcm, to_int, walk_dicts

BASE = "https://www.onthemarket.com"
NAME = "onthemarket"


def g(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _is_listing(d: dict) -> bool:
    return "id" in d and g(d, "details-url", "detailsUrl", "property-title", "propertyTitle", "short-price", "shortPrice") is not None


def _img(v):
    if isinstance(v, dict):
        v = g(v, "default", "url", "src", "webp")
    return v if isinstance(v, str) and v.startswith("http") else None


def _normalise(d: dict, station: dict) -> dict:
    price_text = str(g(d, "short-price", "shortPrice", "price") or "")
    title = str(g(d, "property-title", "propertyTitle", "title") or "")
    beds = to_int(g(d, "bedrooms", "bedrooms-count"))
    if beds is None:
        m = re.search(r"(\d+)\s*bed", title, re.I)
        beds = int(m.group(1)) if m else None
    loc = g(d, "location") or {}
    imgs = [x for x in (_img(i) for i in (g(d, "images", "cover-images", "coverImages") or [])) if x]
    path = str(g(d, "details-url", "detailsUrl") or f"/details/{d['id']}/")
    agent = g(d, "agent") or {}
    labels = " ".join(str(x) for x in (g(d, "labels", "tags") or []) if isinstance(x, str))
    return {
        "key": f"otm:{d['id']}",
        "source": NAME,
        "sourceId": str(d["id"]),
        "url": (BASE + path if path.startswith("/") else path).split("?")[0],
        "title": title,
        "address": str(g(d, "address", "display-address") or ""),
        "pricePcm": price_to_pcm(None, price_text, ""),
        "priceText": price_text,
        "beds": beds,
        "baths": to_int(g(d, "bathrooms")),
        "propertyType": str(g(d, "humanised-property-type", "propertyType") or ""),
        "lat": loc.get("lat") if isinstance(loc, dict) else None,
        "lon": (loc.get("lon") or loc.get("lng")) if isinstance(loc, dict) else None,
        "image": imgs[0] if imgs else None,
        "images": imgs[:12],
        "agent": (agent.get("name") or "") if isinstance(agent, dict) else "",
        "addedText": str(g(d, "days-since-added-reduced", "daysSinceAddedReduced") or ""),
        "summary": str(g(d, "summary") or "")[:500],
        "availableText": g(d, "available-from", "availableFrom"),
        "letAgreed": bool(re.search(r"let agreed|under offer", labels + " " + str(g(d, "status") or ""), re.I)),
        "searchedStation": station["name"],
    }


def _parse_page(page: str, station: dict) -> list[dict]:
    out = {}
    for blob in json_blobs(page):
        for d in walk_dicts(blob):
            if _is_listing(d):
                n = _normalise(d, station)
                feats = d.get("features")
                if isinstance(feats, list):
                    n["features"] = [str(x) for x in feats if isinstance(x, str)][:15]
                out.setdefault(n["key"], n)
    return list(out.values())


def _right_place(items: list[dict], station: dict) -> bool:
    """Reject a slug that resolved to the wrong town (e.g. Mile End, Colchester)."""
    pts = [(float(i["lat"]), float(i["lon"])) for i in items if i.get("lat") and i.get("lon")]
    if not pts:
        return True
    near = sum(1 for lat, lon in pts if haversine_m(lat, lon, station["lat"], station["lon"]) < 5000)
    return near >= len(pts) / 2


def search(cfg: dict, state: dict, f: Fetcher) -> list[dict]:
    s = cfg["search"]
    results: dict[str, dict] = {}
    ok, errors, done = 0, [], set()
    good_slugs = state.setdefault("onthemarketSlugs", {})
    for station in cfg["stations"]:
        slugs = station.get("onthemarket")
        if not slugs:
            continue
        slugs = [slugs] if isinstance(slugs, str) else list(slugs)
        if station["name"] in good_slugs:
            slugs = [good_slugs[station["name"]]]
        params = {
            "max-price": s["maxPricePcm"], "min-bedrooms": s["minBeds"], "max-bedrooms": s["maxBeds"],
            "radius": s["searchRadiusMiles"], "let-agreed": "false", "shared": "false",
            "retirement": "false", "student": "false", "sort-field": "update_date",
        }
        for slug in slugs:
            if slug in done:
                break
            url = f"{BASE}/to-rent/property/{slug}/?{up.urlencode(params)}"
            try:
                r = f.get(url, retries=0 if len(slugs) > 1 else 2)
            except Exception as e:
                errors.append(f"{slug}: {e}")
                print(f"  [onthemarket] {slug} failed: {e}")
                continue
            f.dump(f"search_{slug}", r.text)
            items = _parse_page(r.text, station)
            if not _right_place(items, station):
                print(f"  [onthemarket] {slug}: wrong place, skipping")
                continue
            ok += 1
            done.add(slug)
            if len(slugs) > 1:
                good_slugs[station["name"]] = slug
            found = 0
            for n in items:
                if n["key"] not in results:
                    results[n["key"]] = n
                    found += 1
            print(f"  [onthemarket] {slug}: +{found} (total {len(results)})")
            break
        if ok == 0 and len(errors) >= 3:
            break
    if ok == 0:
        raise RuntimeError("; ".join(errors[:3]) or "no successful searches")
    return list(results.values())


def details(listing: dict, f: Fetcher) -> dict:
    r = f.get(listing["url"])
    page = r.text
    f.dump(f"detail_{listing['sourceId']}", page)
    out: dict = {}
    blobs = list(json_blobs(page))
    for b in blobs:
        for key in ("available-from", "availableFrom", "letAvailableFrom"):
            v = find_key(b, key)
            if isinstance(v, str):
                out["availableText"] = v
                break
        if out.get("availableText"):
            break
    text = html_to_text(page)
    if not out.get("availableText"):
        out["availableText"] = find_available_in_text(text)
    for b in blobs:
        desc = find_key(b, "description")
        if isinstance(desc, str) and len(desc) > 80:
            out["description"] = re.sub(r"<[^>]+>", " ", desc)
            break
    out.setdefault("description", meta_content(page, "og:description"))
    for b in blobs:
        feats = find_key(b, "features")
        if isinstance(feats, list):
            vals = [x if isinstance(x, str) else x.get("feature") for x in feats if isinstance(x, (str, dict))]
            vals = [v for v in vals if isinstance(v, str)]
            if vals:
                out["features"] = vals[:15]
                break
    m = re.search(r"\b(part furnished|unfurnished|furnished)\b", text, re.I)
    if m:
        out["furnished"] = m.group(1).capitalize()
    return out
