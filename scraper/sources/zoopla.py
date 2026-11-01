"""Zoopla: tube-station radius searches. Zoopla sits behind Cloudflare, so this can be blocked."""
from __future__ import annotations

import re
import urllib.parse as up

from ..util import Fetcher, find_available_in_text, find_key, html_to_text, json_blobs, meta_content, price_to_pcm, to_int, walk_dicts

BASE = "https://www.zoopla.co.uk"
NAME = "zoopla"


def _img(v):
    if isinstance(v, dict):
        v = v.get("src") or v.get("url") or v.get("filename")
    if not isinstance(v, str) or not v:
        return None
    if v.startswith("//"):
        return "https:" + v
    if v.startswith("http"):
        return v
    return f"https://lid.zoocdn.com/u/645/430/{v.lstrip('/')}"


def _feature(d: dict, icon: str):
    for ft in d.get("features") or []:
        if isinstance(ft, dict) and icon in str(ft.get("iconId", "")).lower():
            return to_int(ft.get("content"))
    return None


def _normalise(d: dict, station: dict) -> dict | None:
    lid = d.get("listingId") or d.get("listing_id")
    if not lid:
        return None
    price_text = d.get("price") if isinstance(d.get("price"), str) else ""
    amount = d.get("priceUnformatted") or (d.get("price") if isinstance(d.get("price"), (int, float)) else None)
    if isinstance(d.get("pricing"), dict):
        price_text = price_text or d["pricing"].get("label") or ""
        amount = amount or d["pricing"].get("value")
    title = d.get("title") or d.get("propertyType") or ""
    beds = _feature(d, "bed") or to_int(d.get("numBedrooms") or d.get("num_bedrooms") or d.get("bedrooms"))
    if beds is None:
        m = re.search(r"(\d+)\s*bed", title, re.I)
        beds = int(m.group(1)) if m else None
    baths = _feature(d, "bath") or to_int(d.get("numBathrooms") or d.get("bathrooms"))
    pos = d.get("pos") or d.get("location") or {}
    lat = lon = None
    if isinstance(pos, dict):
        coords = pos.get("coordinates") if isinstance(pos.get("coordinates"), dict) else pos
        lat = coords.get("lat") or coords.get("latitude")
        lon = coords.get("lng") or coords.get("lon") or coords.get("longitude")
    imgs = []
    for k in ("imageUris", "images", "gallery"):
        v = d.get(k)
        if isinstance(v, list):
            imgs = [x for x in (_img(i) for i in v) if x]
            if imgs:
                break
    main = _img(d.get("image")) or (imgs[0] if imgs else None)
    uris = d.get("listingUris") or {}
    path = uris.get("detail") if isinstance(uris, dict) else None
    url = BASE + path if path and path.startswith("/") else f"{BASE}/to-rent/details/{lid}/"
    flag = str(d.get("flag") or "")
    branch = d.get("branch") or {}
    tags = " ".join(str(x) for x in (d.get("tags") or []) if isinstance(x, (str, dict)))
    return {
        "key": f"zp:{lid}",
        "source": NAME,
        "sourceId": str(lid),
        "url": url.split("?")[0],
        "title": title,
        "address": d.get("address") or d.get("displayAddress") or "",
        "pricePcm": price_to_pcm(amount, price_text, ""),
        "priceText": price_text,
        "beds": beds,
        "baths": baths,
        "propertyType": d.get("propertyType") or "",
        "lat": lat,
        "lon": lon,
        "image": main,
        "images": imgs[:12],
        "agent": branch.get("name") if isinstance(branch, dict) else "",
        "addedText": flag or d.get("publishedOn") or d.get("publishedOnLabel") or "",
        "summary": (d.get("summaryDescription") or d.get("description") or "")[:500] if isinstance(d.get("summaryDescription") or d.get("description") or "", str) else "",
        "availableText": d.get("availableFrom") or d.get("availableFromLabel"),
        "letAgreed": bool(re.search(r"let agreed|under offer", f"{flag} {tags}", re.I)),
        "searchedStation": station["name"],
    }


def search(cfg: dict, state: dict, f: Fetcher) -> list[dict]:
    s = cfg["search"]
    results: dict[str, dict] = {}
    ok = 0
    errors = []
    for station in cfg["stations"]:
        slug = station.get("zoopla")
        if not slug:
            continue
        params = {
            "beds_min": s["minBeds"], "beds_max": s["maxBeds"],
            "price_frequency": "per_month", "price_max": s["maxPricePcm"],
            "radius": s["searchRadiusMiles"], "results_sort": "newest_listings",
            "search_source": "to-rent", "is_shared_accommodation": "false",
            "is_student_accommodation": "false", "is_retirement_home": "false",
            "include_rented": "false",
        }
        url = f"{BASE}/to-rent/property/station/tube/{slug}/?{up.urlencode(params)}"
        try:
            r = f.get(url)
        except Exception as e:
            errors.append(f"{station['name']}: {e}")
            print(f"  [zoopla] {station['name']} failed: {e}")
            if len(errors) >= 2 and ok == 0:
                break  # blocked; don't keep hammering
            continue
        f.dump(f"search_{slug}", r.text)
        if "Just a moment" in r.text[:5000] or "cf-chl" in r.text[:20000]:
            errors.append(f"{station['name']}: Cloudflare challenge")
            if ok == 0 and len(errors) >= 2:
                break
            continue
        ok += 1
        found = 0
        for blob in json_blobs(r.text):
            for d in walk_dicts(blob):
                if ("listingId" in d or "listing_id" in d) and ("price" in d or "pricing" in d or "priceUnformatted" in d):
                    n = _normalise(d, station)
                    if n and n["key"] not in results:
                        results[n["key"]] = n
                        found += 1
        print(f"  [zoopla] {station['name']}: +{found} (total {len(results)})")
    if ok == 0:
        raise RuntimeError("; ".join(errors[:3]) or "no successful searches")
    return list(results.values())


def details(listing: dict, f: Fetcher) -> dict:
    r = f.get(listing["url"])
    page = r.text
    f.dump(f"detail_{listing['sourceId']}", page)
    out: dict = {}
    blobs = list(json_blobs(page))
    for key in ("availableFrom", "availableFromLabel", "available_from", "availabilityDate"):
        for b in blobs:
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
        desc = find_key(b, "detailedDescription") or find_key(b, "description")
        if isinstance(desc, str) and len(desc) > 80:
            out["description"] = re.sub(r"<[^>]+>", " ", desc)
            break
    if not out.get("description"):
        out["description"] = meta_content(page, "og:description")
    for b in blobs:
        feats = find_key(b, "bullets") or find_key(b, "featuresList")
        if isinstance(feats, list) and all(isinstance(x, str) for x in feats):
            out["features"] = feats[:15]
            break
    m = re.search(r"(\d[\d,]*)\s*sq\.?\s*ft", text, re.I)
    if m:
        out["sizeSqft"] = to_int(m.group(1))
    if re.search(r"\b(furnished|unfurnished|part furnished)\b", text, re.I):
        out["furnished"] = re.search(r"\b(part furnished|unfurnished|furnished)\b", text, re.I).group(1).capitalize()
    if re.search(r"this property is no longer available|let agreed", text[:4000], re.I):
        out["gone"] = True
    return out
