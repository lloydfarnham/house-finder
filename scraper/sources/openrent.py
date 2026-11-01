"""OpenRent: landlord-direct lets (no agent fees). Search around each station."""
from __future__ import annotations

import html as htmllib
import json
import re
import urllib.parse as up

from ..util import Fetcher, find_available_in_text, html_to_text, meta_content, price_to_pcm, to_int

BASE = "https://www.openrent.co.uk"
NAME = "openrent"


def _js_array(page: str, name: str):
    m = re.search(rf"{name}\s*=\s*(\[[^\]]*\])", page)
    if not m:
        return []
    try:
        return json.loads(m.group(1).replace("'", '"'))
    except Exception:
        return [x.strip() for x in m.group(1).strip("[]").split(",")]


def search(cfg: dict, state: dict, f: Fetcher) -> list[dict]:
    s = cfg["search"]
    results: dict[str, dict] = {}
    ok, errors = 0, []
    km = round(s["searchRadiusMiles"] * 1.609, 1)
    for station in cfg["stations"]:
        term = f"{station['name']} Station, London"
        params = {
            "term": term, "area": km, "prices_max": s["maxPricePcm"],
            "bedrooms_min": s["minBeds"], "bedrooms_max": s["maxBeds"],
            "isLive": "true", "acceptStudents": "", "sortType": "1",
        }
        url = f"{BASE}/properties-to-rent/london?{up.urlencode(params)}"
        try:
            r = f.get(url)
        except Exception as e:
            errors.append(f"{station['name']}: {e}")
            print(f"  [openrent] {station['name']} failed: {e}")
            if ok == 0 and len(errors) >= 2:
                break
            continue
        page = r.text
        f.dump(f"search_{station['name']}", page)
        ok += 1
        ids = [str(x) for x in _js_array(page, "PROPERTYIDS")]
        lats = _js_array(page, "PROPERTYLISTLATITUDES")
        lons = _js_array(page, "PROPERTYLISTLONGITUDES")
        coords = {i: (float(lats[n]), float(lons[n])) for n, i in enumerate(ids) if n < len(lats) and n < len(lons)}
        found = 0
        # Each card is a link to /property-to-rent/<area>/<slug>/<id>
        for m in re.finditer(r'<a[^>]+href="(/property-to-rent/[^"]+/(\d+))"[^>]*>(.*?)</a>', page, re.S):
            path, pid, inner = m.group(1), m.group(2), m.group(3)
            key = f"or:{pid}"
            if key in results:
                continue
            text = html_to_text(inner)
            pm = re.search(r"£\s?([\d,]+)\s*(per month|pcm|per week|pw)?", text, re.I)
            if not pm:
                continue
            title_m = re.search(r"(\d+\s*Bed[^\n£]*)", text, re.I)
            title = (title_m.group(1).strip() if title_m else text.split("\n")[0])[:140]
            beds_m = re.search(r"(\d+)\s*Bed", text, re.I)
            img = re.search(r'(?:data-src|src)="((?:https?:)?//[^"]+\.(?:jpe?g|png|webp)[^"]*)"', inner)
            image = img.group(1) if img else None
            if image and image.startswith("//"):
                image = "https:" + image
            lat, lon = coords.get(pid, (None, None))
            address = title.split(",", 1)[1].strip() if "," in title else title
            results[key] = {
                "key": key, "source": NAME, "sourceId": pid,
                "url": BASE + htmllib.unescape(path),
                "title": title.split(",")[0].strip(),
                "address": address,
                "pricePcm": price_to_pcm(pm.group(1), pm.group(0), ""),
                "priceText": pm.group(0),
                "beds": int(beds_m.group(1)) if beds_m else None,
                "baths": None, "propertyType": "",
                "lat": lat, "lon": lon, "image": image, "images": [image] if image else [],
                "agent": "OpenRent (landlord direct)", "addedText": "",
                "summary": "", "availableText": find_available_in_text(text),
                "letAgreed": bool(re.search(r"let agreed|no longer available", text, re.I)),
                "searchedStation": station["name"],
            }
            found += 1
        print(f"  [openrent] {station['name']}: +{found} (total {len(results)})")
    if ok == 0:
        raise RuntimeError("; ".join(errors[:3]) or "no successful searches")
    return list(results.values())


def details(listing: dict, f: Fetcher) -> dict:
    r = f.get(listing["url"])
    page = r.text
    f.dump(f"detail_{listing['sourceId']}", page)
    text = html_to_text(page)
    out: dict = {}
    m = re.search(r"Available From\s*\n?\s*([^\n]+)", text, re.I)
    out["availableText"] = m.group(1).strip() if m else find_available_in_text(text)
    out["description"] = meta_content(page, "og:description") or meta_content(page, "description")
    m = re.search(r"Furnishing\s*\n?\s*([^\n]+)", text, re.I)
    if m:
        out["furnished"] = m.group(1).strip()[:30]
    m = re.search(r"Deposit\s*\n?\s*(£[\d,.]+)", text, re.I)
    if m:
        out["deposit"] = m.group(1)
    if not listing.get("lat"):
        lat = re.search(r'data-lat="([\d.-]+)"', page) or re.search(r'"latitude"\s*:\s*"?([\d.-]+)', page)
        lon = re.search(r'data-lng="([\d.-]+)"', page) or re.search(r'"longitude"\s*:\s*"?([\d.-]+)', page)
        if lat and lon:
            out["lat"], out["lon"] = float(lat.group(1)), float(lon.group(1))
    img = meta_content(page, "og:image")
    if img and not listing.get("image"):
        out["image"] = img
    if re.search(r"this property has been let|no longer available", text[:5000], re.I):
        out["gone"] = True
    return out
