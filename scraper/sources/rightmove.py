"""Rightmove: station-radius searches, plus listing pages for availability dates."""
from __future__ import annotations

import re
import urllib.parse as up

from ..util import Fetcher, find_key, json_blobs, price_to_pcm, to_int, walk_dicts

BASE = "https://www.rightmove.co.uk"
NAME = "rightmove"


def _resolve_station(f: Fetcher, station: dict, cache: dict) -> str | None:
    """Turn 'Bethnal Green' into Rightmove's STATION^944 style identifier."""
    if station.get("rightmoveId"):
        return station["rightmoveId"]
    if station["name"] in cache:
        return cache[station["name"]]
    query = f"{station['name'].replace('-', ' ')} Station"
    ident = None
    try:
        r = f.get(f"https://los.rightmove.co.uk/typeahead?query={up.quote(query)}&limit=10&exclude=STREET", json_api=True)
        for m in r.json().get("matches", []):
            if m.get("type") == "STATION":
                ident = f"STATION^{m['id']}"
                break
    except Exception as e:
        print(f"  [rightmove] typeahead failed for {query}: {e}")
    if not ident:
        # Older endpoint: tokens of two characters
        q = query.upper().replace(" ", "_")
        tokens = "/".join(q[i:i + 2] for i in range(0, len(q), 2))
        try:
            r = f.get(f"{BASE}/typeAhead/uknostreet/{tokens}/", json_api=True)
            for loc in r.json().get("typeAheadLocations", []):
                if loc.get("locationIdentifier", "").startswith("STATION^"):
                    ident = loc["locationIdentifier"]
                    break
        except Exception as e:
            print(f"  [rightmove] legacy typeahead failed for {query}: {e}")
    if ident:
        cache[station["name"]] = ident
    return ident


def _params(ident: str, s: dict, index: int) -> dict:
    return {
        "locationIdentifier": ident,
        "radius": str(s["searchRadiusMiles"]),
        "maxPrice": str(s["maxPricePcm"]),
        "minBedrooms": str(s["minBeds"]),
        "maxBedrooms": str(s["maxBeds"]),
        "includeLetAgreed": "false",
        "dontShow": "houseShare,retirement,student",
        "sortType": "6",
        "index": str(index),
        "channel": "RENT",
        "transactionType": "LETTING",
        "numberOfPropertiesPerPage": "24",
        "viewType": "LIST",
        "areaSizeUnit": "sqft",
        "currencyCode": "GBP",
    }


def _extract_properties(obj) -> list[dict]:
    out, seen = [], set()
    for d in walk_dicts(obj):
        if "id" in d and "price" in d and ("bedrooms" in d or "displayAddress" in d) and isinstance(d.get("price"), dict):
            if d["id"] not in seen:
                seen.add(d["id"])
                out.append(d)
    return out


def _search_page(f: Fetcher, ident: str, s: dict, index: int) -> tuple[list[dict], int | None]:
    params = _params(ident, s, index)
    # 1) the JSON API the site itself uses
    try:
        r = f.get(f"{BASE}/api/_search?{up.urlencode(params)}", json_api=True)
        data = r.json()
        props = _extract_properties(data)
        total = to_int(find_key(data, "resultCount")) or to_int(find_key(data, "totalAvailableResults"))
        if props or total == 0:
            return props, total
    except Exception as e:
        print(f"  [rightmove] api search failed ({e}); trying the HTML page")
    # 2) the HTML search page with embedded JSON
    r = f.get(f"{BASE}/property-to-rent/find.html?{up.urlencode(params)}")
    f.dump(f"search_{ident}_{index}", r.text)
    props, total = [], None
    for blob in json_blobs(r.text):
        props.extend(_extract_properties(blob))
        total = total or to_int(find_key(blob, "resultCount"))
    uniq = {p["id"]: p for p in props}
    return list(uniq.values()), total


def _normalise(p: dict, station: dict) -> dict:
    price = p.get("price") or {}
    disp = (price.get("displayPrices") or [{}])[0].get("displayPrice", "")
    images = [i.get("srcUrl") or i.get("url") for i in (p.get("propertyImages") or {}).get("images", []) if isinstance(i, dict)]
    images = [i for i in images if i]
    main = (p.get("propertyImages") or {}).get("mainImageSrc") or (images[0] if images else None)
    loc = p.get("location") or {}
    url = p.get("propertyUrl") or f"/properties/{p['id']}"
    url = BASE + url.split("#")[0] if url.startswith("/") else url.split("#")[0]
    status = (p.get("displayStatus") or "").lower()
    return {
        "key": f"rm:{p['id']}",
        "source": NAME,
        "sourceId": str(p["id"]),
        "url": url,
        "title": p.get("propertyTypeFullDescription") or p.get("heading") or f"{p.get('bedrooms')} bedroom property",
        "address": p.get("displayAddress") or "",
        "pricePcm": price_to_pcm(price.get("amount"), disp, price.get("frequency", "")),
        "priceText": disp,
        "beds": to_int(p.get("bedrooms")),
        "baths": to_int(p.get("bathrooms")),
        "propertyType": p.get("propertySubType") or "",
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
        "image": main,
        "images": images[:12],
        "agent": (p.get("customer") or {}).get("brandTradingName") or p.get("formattedBranchName") or "",
        "addedText": p.get("addedOrReduced") or "",
        "firstVisible": p.get("firstVisibleDate"),
        "summary": (p.get("summary") or "")[:500],
        "availableText": p.get("letAvailableDate"),
        "features": [str(x.get("description") if isinstance(x, dict) else x) for x in (p.get("keyFeatures") or [])][:15],
        "letAgreed": "let agreed" in status or "under offer" in status,
        "searchedStation": station["name"],
    }


def search(cfg: dict, state: dict, f: Fetcher) -> list[dict]:
    s = cfg["search"]
    cache = state.setdefault("rightmoveStationIds", {})
    results: dict[str, dict] = {}
    resolved = 0
    for station in cfg["stations"]:
        ident = _resolve_station(f, station, cache)
        if not ident:
            print(f"  [rightmove] could not resolve station {station['name']}")
            continue
        resolved += 1
        index, pages = 0, 0
        while pages < 4:
            props, total = _search_page(f, ident, s, index)
            for p in props:
                n = _normalise(p, station)
                results.setdefault(n["key"], n)
            pages += 1
            index += 24
            if not props or total is None or index >= total:
                break
        print(f"  [rightmove] {station['name']} ({ident}): running total {len(results)}")
    if resolved == 0:
        raise RuntimeError("could not resolve any station identifiers")
    return list(results.values())


def details(listing: dict, f: Fetcher) -> dict:
    r = f.get(listing["url"])
    page = r.text
    f.dump(f"detail_{listing['sourceId']}", page)
    out: dict = {}
    model = None
    for blob in json_blobs(page):
        if find_key(blob, "propertyData") is not None or find_key(blob, "lettings") is not None:
            model = blob
            break
    src = model if model is not None else {}
    lettings = find_key(src, "lettings") or {}
    if isinstance(lettings, dict):
        out["availableText"] = lettings.get("letAvailableDate")
        out["deposit"] = lettings.get("deposit")
        out["furnished"] = lettings.get("furnishType")
        out["minTermMonths"] = lettings.get("minimumTermInMonths")
    if not out.get("availableText"):
        m = re.search(r'"letAvailableDate"\s*:\s*"([^"]+)"', page)
        if m:
            out["availableText"] = m.group(1)
    feats = find_key(src, "keyFeatures")
    if isinstance(feats, list):
        out["features"] = [str(x) for x in feats if isinstance(x, (str, int))][:15]
    text = find_key(src, "text") or {}
    desc = text.get("description") if isinstance(text, dict) else None
    if desc:
        out["description"] = re.sub(r"<[^>]+>", " ", desc)
    status = find_key(src, "status") or {}
    if isinstance(status, dict) and status.get("published") is False:
        out["gone"] = True
    sizes = find_key(src, "sizings")
    if isinstance(sizes, list):
        for sz in sizes:
            if isinstance(sz, dict) and sz.get("unit") == "sqft" and sz.get("minimumSize"):
                out["sizeSqft"] = to_int(sz["minimumSize"])
    return out
