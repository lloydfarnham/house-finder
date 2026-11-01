"""OnTheMarket: area searches with a radius (results are geo-filtered afterwards)."""
from __future__ import annotations

import re
import urllib.parse as up

from ..util import Fetcher, find_available_in_text, find_key, html_to_text, json_blobs, meta_content, price_to_pcm, to_int, walk_dicts

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


def search(cfg: dict, state: dict, f: Fetcher) -> list[dict]:
    s = cfg["search"]
    results: dict[str, dict] = {}
    ok, errors, done = 0, [], set()
    for station in cfg["stations"]:
        slug = station.get("onthemarket")
        if not slug or slug in done:
            continue
        done.add(slug)
        params = {
            "max-price": s["maxPricePcm"], "min-bedrooms": s["minBeds"], "max-bedrooms": s["maxBeds"],
            "radius": s["searchRadiusMiles"], "let-agreed": "false", "shared": "false",
            "retirement": "false", "student": "false", "sort-field": "update_date",
        }
        url = f"{BASE}/to-rent/property/{slug}/?{up.urlencode(params)}"
        try:
            r = f.get(url)
        except Exception as e:
            errors.append(f"{slug}: {e}")
            print(f"  [onthemarket] {slug} failed: {e}")
            if ok == 0 and len(errors) >= 2:
                break
            continue
        f.dump(f"search_{slug}", r.text)
        ok += 1
        found = 0
        for blob in json_blobs(r.text):
            for d in walk_dicts(blob):
                if _is_listing(d):
                    n = _normalise(d, station)
                    if n["key"] not in results:
                        results[n["key"]] = n
                        found += 1
        print(f"  [onthemarket] {slug}: +{found} (total {len(results)})")
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
