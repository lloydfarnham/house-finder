"""Shared helpers: HTTP session, JSON extraction from pages, geo maths, date parsing."""
from __future__ import annotations

import html as htmllib
import json
import math
import os
import random
import re
import time
from datetime import date, datetime
from pathlib import Path

try:
    from curl_cffi import requests as http  # browser-like TLS fingerprint
    _IMPERSONATE = {"impersonate": "chrome"}
except Exception:  # pragma: no cover - fallback for local dev
    import requests as http  # type: ignore
    _IMPERSONATE = {}

DEBUG = os.environ.get("DEBUG") == "1"
DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


class Fetcher:
    """A polite HTTP client: one session per site, small random delays, retries."""

    def __init__(self, name: str, min_delay: float = 1.5, max_delay: float = 3.5):
        self.name = name
        self.session = http.Session()
        self.min_delay, self.max_delay = min_delay, max_delay
        self._last = 0.0
        self.requests = 0

    def get(self, url: str, *, json_api: bool = False, retries: int = 2, **kw):
        headers = dict(HEADERS)
        if json_api:
            headers["Accept"] = "application/json, text/plain, */*"
        headers.update(kw.pop("headers", {}))
        last_err = None
        for attempt in range(retries + 1):
            wait = self._last + random.uniform(self.min_delay, self.max_delay) - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                self.requests += 1
                r = self.session.get(url, headers=headers, timeout=30, **_IMPERSONATE, **kw)
                self._last = time.time()
                if r.status_code == 200:
                    return r
                last_err = RuntimeError(f"HTTP {r.status_code} for {url}")
                if r.status_code in (403, 404, 410):
                    break
            except Exception as e:  # network errors
                self._last = time.time()
                last_err = e
            time.sleep(2 * (attempt + 1))
        raise last_err  # type: ignore[misc]

    def dump(self, label: str, text: str):
        if not DEBUG:
            return
        DEBUG_DIR.mkdir(exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", label)[:80]
        (DEBUG_DIR / f"{self.name}_{safe}.html").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- JSON extraction

_decoder = json.JSONDecoder()


def _raw_decode_at(text: str, start: int):
    try:
        obj, _ = _decoder.raw_decode(text, start)
        return obj
    except Exception:
        return None


def json_blobs(page: str):
    """Yield every JSON structure we can find embedded in an HTML page."""
    # <script type="application/json"> and __NEXT_DATA__ and ld+json
    for m in re.finditer(r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', page, re.S):
        try:
            yield json.loads(m.group(1))
        except Exception:
            pass
    # window.SOMETHING = {...}
    for m in re.finditer(r'window\.([A-Za-z_][\w]*)\s*=\s*(?=[\[{])', page):
        obj = _raw_decode_at(page, m.end())
        if obj is not None:
            yield obj
    # Next.js app router flight data: self.__next_f.push([1,"..."])
    chunks = []
    for m in re.finditer(r'self\.__next_f\.push\(\[\d+\s*,\s*("(?:[^"\\]|\\.)*")\]\)', page, re.S):
        try:
            chunks.append(json.loads(m.group(1)))
        except Exception:
            pass
    if chunks:
        flight = "".join(chunks)
        for line in flight.split("\n"):
            mm = re.match(r"^[0-9a-zA-Z]+:(?=[\[{])", line)
            if not mm:
                continue
            obj = _raw_decode_at(line, mm.end())
            if obj is not None:
                yield obj
        # some payloads are embedded mid-line; look for listing-ish objects too
        for mm in re.finditer(r'\{"(?:listingId|listing_id|propertyId)"', flight):
            obj = _raw_decode_at(flight, mm.start())
            if obj is not None:
                yield obj


def walk_dicts(obj):
    """Iteratively yield every dict inside a nested JSON structure."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def find_key(obj, key: str):
    """Return the first value for `key` anywhere in obj."""
    for d in walk_dicts(obj):
        if key in d and d[key] not in (None, "", [], {}):
            return d[key]
    return None


def html_to_text(page: str) -> str:
    page = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<br\s*/?>|</(p|div|li|h\d|tr|dt|dd)>", "\n", page, flags=re.I)
    page = re.sub(r"<[^>]+>", " ", page)
    page = htmllib.unescape(page)
    page = re.sub(r"[ \t\xa0]+", " ", page)
    return re.sub(r"\n\s*\n+", "\n", page).strip()


def meta_content(page: str, prop: str) -> str | None:
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', page)
    if not m:
        m = re.search(rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{re.escape(prop)}"', page)
    return htmllib.unescape(m.group(1)) if m else None


# ---------------------------------------------------------------- numbers & prices

def to_int(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"\d[\d,]*", str(v))
    return int(m.group(0).replace(",", "")) if m else None


def price_to_pcm(amount, text: str = "", frequency: str = "") -> int | None:
    """Normalise a rent to £ per calendar month."""
    n = to_int(amount)
    if n is None:
        n = to_int(text)
    if n is None:
        return None
    blob = f"{frequency} {text}".lower()
    if re.search(r"\bpw\b|per week|weekly|/ ?w(ee)?k", blob):
        return round(n * 52 / 12)
    if re.search(r"\bpa\b|per annum|yearly|annual", blob):
        return round(n / 12)
    return n


# ---------------------------------------------------------------- geo

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


WALK_M_PER_MIN = 80        # ~4.8 km/h
WALK_DETOUR = 1.3          # streets are not straight lines
CYCLE_M_PER_MIN = 300      # ~18 km/h commuter pace
CYCLE_DETOUR = 1.25


def walk_minutes(dist_m: float) -> int:
    return max(1, round(dist_m * WALK_DETOUR / WALK_M_PER_MIN))


def cycle_minutes(dist_m: float) -> int:
    return max(1, round(dist_m * CYCLE_DETOUR / CYCLE_M_PER_MIN))


# ---------------------------------------------------------------- dates

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_NOW_WORDS = re.compile(r"\b(now|immediately|immediate|asap|today)\b", re.I)


def parse_available(text, today: date | None = None):
    """Parse an availability string. Returns ('now', None), ('date', date) or None."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    today = today or date.today()
    if _NOW_WORDS.search(s):
        return ("now", None)
    # ISO 2027-01-01
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return ("date", date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    # 01/01/2027 or 1/1/27 (UK order)
    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return ("date", date(y, mo, d))
        except ValueError:
            pass
    # 1st January 2027 / 1 Jan 2027 / January 2027 / 1st Jan
    for m in re.finditer(r"(?:\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?)?\b([A-Za-z]{3,9})\b\.?,?\s*(\d{4})?", s):
        word = m.group(2).lower()
        mon = MONTHS.get(word[:3])
        if not mon or not (m.group(1) or m.group(3)):
            continue
        # avoid matching random words that start like a month ("mark", "decent")
        full = ["january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december"][mon - 1]
        if not (full.startswith(word) or word in ("sept",)):
            continue
        d = int(m.group(1)) if m.group(1) else 1
        if m.group(3):
            y = int(m.group(3))
        else:
            y = today.year if mon >= today.month - 1 else today.year + 1
        try:
            return ("date", date(y, mon, d))
        except ValueError:
            return None
    return None


AVAILABLE_TEXT_RE = re.compile(
    r"(?:let\s+)?available\s*(?:from|date|on)?\s*[:\-]?\s*"
    r"(now|immediately|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?\s*\d{0,4}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Za-z]{3,9}\s+\d{4})",
    re.I,
)


def find_available_in_text(text: str):
    m = AVAILABLE_TEXT_RE.search(text or "")
    return m.group(1).strip() if m else None


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
