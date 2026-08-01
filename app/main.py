"""
AdSpy backend v3 — Meta Ad Library extractor + media proxy.

v3 parses against Meta's REAL verified structure rather than a guess:

    ...deeplink_ad_archive_result.deeplink_ad_archive     <- ad object
        start_date / end_date / is_active
        publisher_platform / collation_count / total_active_time
        reach_estimate / spend / currency
        snapshot                                          <- creative
            body{text} / title / caption / link_url
            cta_text / cta_type / display_format
            page_like_count / page_categories
            videos[0].video_hd_url                        <- media lives in a list
            images[0] / cards[] / extra_videos[]

Standalone parse check (no network, uses the file test3.py saved):
    python3 -m app.main_v3 ad_page.html
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

import razorpay
from curl_cffi.requests import AsyncSession
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

raw_origins = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000"
)
ALLOWED_ORIGINS = [o.strip() for o in raw_origins.split(",") if o.strip()]
ALLOWED_MEDIA_SUFFIXES = (".fbcdn.net", ".cdninstagram.com")

IMPERSONATE = "chrome"
DEFAULT_COUNTRY = "ALL"
CHALLENGE_ROUNDS = 4
RATE_LIMIT_PER_MIN = 12
SESSION_MAX_AGE_S = 30 * 60

CACHE_TTL_S = 30 * 60  # 30 minutes
CACHE_MAX_SIZE = 500

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
ALLOWED_PAYMENT_AMOUNTS = {4900, 19900, 49900}

CHALLENGE_RE = re.compile(r"""fetch\(['"](/__rd_verify_[^'"]+)['"]""")
SCRIPT_JSON_RE = re.compile(
    r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', re.DOTALL
)

app = FastAPI(title="AdSpy API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------- #
# In-memory LRU Ad Cache (TTL 30 minutes, max 500 entries)
# --------------------------------------------------------------------------- #


class ExtractCache:
    def __init__(self, max_size: int = CACHE_MAX_SIZE, ttl_s: float = CACHE_TTL_S):
        self.max_size = max_size
        self.ttl_s = ttl_s
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self.hits: int = 0
        self.misses: int = 0

    def get(self, ad_id: str) -> dict | None:
        if not ad_id or ad_id not in self._cache:
            self.misses += 1
            return None

        entry = self._cache[ad_id]
        now = time.time()
        if now - entry["cached_at"] > self.ttl_s:
            del self._cache[ad_id]
            self.misses += 1
            return None

        self._cache.move_to_end(ad_id)
        self.hits += 1

        cached_payload = dict(entry["payload"])
        cached_payload["cache_hit"] = True
        cached_payload["cached_at"] = entry["cached_at"]
        return cached_payload

    def set(self, ad_id: str, payload: dict) -> None:
        if not ad_id:
            return
        now = time.time()

        if ad_id not in self._cache and len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

        self._cache[ad_id] = {
            "cached_at": now,
            "payload": payload,
        }
        self._cache.move_to_end(ad_id)

    @property
    def size(self) -> int:
        now = time.time()
        expired = [k for k, v in self._cache.items() if now - v["cached_at"] > self.ttl_s]
        for k in expired:
            del self._cache[k]
        return len(self._cache)

    @property
    def stats(self) -> dict:
        total = self.hits + self.misses
        hit_rate = round(self.hits / total, 4) if total > 0 else 0.0
        return {
            "cache_size": self.size,
            "cache_hits": self.hits,
            "cache_misses": self.misses,
            "cache_hit_rate": hit_rate,
        }


_extract_cache = ExtractCache()

# --------------------------------------------------------------------------- #
# Rate limiting (in-memory; move to Redis before running multiple workers)
# --------------------------------------------------------------------------- #

_hits: dict[str, deque[float]] = defaultdict(deque)


def rate_limit(request: Request) -> None:
    ip = request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else "unknown"
    )
    now = time.time()
    bucket = _hits[ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(429, "Too many requests. Wait a minute and retry.")
    bucket.append(now)


# --------------------------------------------------------------------------- #
# Shared Meta session — the rd_challenge cookie survives, so we solve the
# challenge roughly once per process instead of once per user.
# --------------------------------------------------------------------------- #

_session: AsyncSession | None = None
_session_born: float = 0.0
_session_lock = asyncio.Lock()


async def get_session(force_new: bool = False) -> AsyncSession:
    global _session, _session_born

    async with _session_lock:
        stale = time.time() - _session_born > SESSION_MAX_AGE_S
        if _session is not None and not force_new and not stale:
            return _session

        if _session is not None:
            try:
                _session.close()
            except Exception:
                pass

        session = AsyncSession(impersonate=IMPERSONATE, timeout=30)
        try:
            await session.get("https://www.facebook.com/")
        except Exception:
            pass

        _session, _session_born = session, time.time()
        return session


def ad_library_url(ad_id: str, country: str = DEFAULT_COUNTRY) -> str:
    return (
        "https://www.facebook.com/ads/library/"
        f"?active_status=active&ad_type=all&country={country}&id={ad_id}"
        "&is_targeted_country=false&media_type=all&search_type=keyword_unordered"
    )


async def fetch_with_challenge(session: AsyncSession, url: str):
    """
    Meta answers the first request with 403 + a ~480 byte page whose JS POSTs to
    /__rd_verify_<token>?challenge=N and reloads. That handshake is mechanical,
    so we replay it over plain HTTP.
    """
    resp = await session.get(url)

    for _ in range(CHALLENGE_ROUNDS):
        if resp.status_code == 200 and "deeplink_ad_archive" in resp.text:
            return resp

        match = CHALLENGE_RE.search(resp.text)
        if not match:
            return resp

        await session.post(
            "https://www.facebook.com" + match.group(1),
            headers={
                "Referer": url,
                "Origin": "https://www.facebook.com",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        resp = await session.get(url)

    return resp


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def extract_ad_id(raw: str) -> str:
    raw = raw.strip()
    if raw.isdigit():
        return raw
    qs = parse_qs(urlparse(raw).query)
    for key in ("id", "ad_archive_id"):
        if key in qs and qs[key][0].isdigit():
            return qs[key][0]
    m = re.search(r"(\d{10,})", raw)
    if m:
        return m.group(1)
    raise HTTPException(400, "Could not find an ad id in that URL.")


def iter_dicts(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_dicts(item)


def find_ad_object(html: str, ad_id: str | None = None) -> dict:
    """
    Locate the ad-archive object: a dict holding a 'snapshot' child. Meta nests
    it deep and renames wrappers between releases, so we search by shape rather
    than by a fixed path.
    """
    candidates: list[dict] = []

    for blob in SCRIPT_JSON_RE.findall(html):
        try:
            parsed = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        for node in iter_dicts(parsed):
            if isinstance(node.get("snapshot"), dict):
                candidates.append(node)

    if not candidates:
        print(f"[ERROR] Parse failure for ad_id={ad_id}: No candidates with snapshot found in HTML")
        raise HTTPException(
            502,
            "Meta returned a page without ad data. The ad may be inactive, "
            "region-locked, or Meta changed its payload shape.",
        )

    if ad_id:
        for node in candidates:
            if str(node.get("ad_archive_id") or "") == str(ad_id):
                return node
        print(f"[ERROR] Parse failure for ad_id={ad_id}: No candidate matched requested ad_id")
        raise HTTPException(
            502,
            f"Meta returned a page without data for ad ID {ad_id}. The ad may be inactive, "
            "region-locked, deleted, or non-existent.",
        )

    # Otherwise take the one carrying the most populated fields (used during offline checks when ad_id is omitted).
    return max(
        candidates,
        key=lambda n: sum(1 for v in n.values() if v not in (None, "", [], {})),
    )


def decode_efg(media_url: str | None) -> dict:
    """
    fbcdn URLs carry a base64 `efg` param with real creative metadata:
        {"duration_s": 31, "asset_age_days": 140, "xpv_asset_id": ...}
    Free signal that godofroas.com throws away.
    """
    if not media_url:
        return {}
    raw = parse_qs(urlparse(media_url).query).get("efg", [None])[0]
    if not raw:
        return {}
    raw = unquote(raw)
    raw += "=" * (-len(raw) % 4)
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8", "ignore"))
    except (binascii.Error, json.JSONDecodeError, ValueError):
        return {}


def text_of(value: Any) -> str | None:
    """Meta wraps copy as either a plain string or {"text": "..."}."""
    if isinstance(value, dict):
        value = value.get("text")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def first(items: Any, *keys: str) -> str | None:
    """First non-empty value for any of `keys` across a list of dicts."""
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if value:
                return value
    return None


def collect_media(snap: dict) -> dict:
    videos = (snap.get("videos") or []) + (snap.get("extra_videos") or [])
    images = (snap.get("images") or []) + (snap.get("extra_images") or [])

    return {
        "video_hd_url": first(videos, "video_hd_url"),
        "video_sd_url": first(videos, "video_sd_url"),
        "preview_image_url": first(videos, "video_preview_image_url"),
        "image_url": first(images, "original_image_url", "resized_image_url", "url"),
        "video_count": len(videos),
        "image_count": len(images),
    }


def normalise(ad: dict, ad_id: str, source_url: str) -> dict:
    snap = ad.get("snapshot") or {}
    media = collect_media(snap)
    efg = decode_efg(media["video_hd_url"] or media["video_sd_url"])

    cards = []
    for card in snap.get("cards") or []:
        if not isinstance(card, dict):
            continue
        card_media = collect_media(card)
        cards.append(
            {
                "title": text_of(card.get("title")),
                "body": text_of(card.get("body")),
                "link_url": card.get("link_url"),
                "cta_text": card.get("cta_text"),
                "image_url": card_media["image_url"]
                or card.get("original_image_url")
                or card.get("resized_image_url"),
                "video_hd_url": card_media["video_hd_url"] or card.get("video_hd_url"),
            }
        )

    return {
        "ad_id": str(ad.get("ad_archive_id") or ad_id),
        # --- advertiser ----------------------------------------------------- #
        "page_name": ad.get("page_name") or snap.get("page_name"),
        "page_id": ad.get("page_id") or snap.get("page_id"),
        "page_profile_pic": snap.get("page_profile_picture_url"),
        "page_url": snap.get("page_profile_uri"),
        "page_like_count": snap.get("page_like_count"),
        "page_categories": snap.get("page_categories") or [],
        # --- creative copy -------------------------------------------------- #
        "title": text_of(snap.get("title")),
        "body": text_of(snap.get("body")),
        "caption": snap.get("caption"),
        "link_description": snap.get("link_description"),
        "link_url": snap.get("link_url"),
        "cta_text": snap.get("cta_text"),
        "cta_type": snap.get("cta_type"),
        "display_format": snap.get("display_format"),
        # --- media ---------------------------------------------------------- #
        "video_hd_url": media["video_hd_url"],
        "video_sd_url": media["video_sd_url"],
        "image_url": media["image_url"],
        "preview_image_url": media["preview_image_url"],
        "video_count": media["video_count"],
        "image_count": media["image_count"],
        "cards": cards,
        # --- delivery ------------------------------------------------------- #
        "start_date_ts": ad.get("start_date"),
        "end_date_ts": ad.get("end_date"),
        "is_active": ad.get("is_active"),
        "publisher_platforms": ad.get("publisher_platform") or [],
        "total_active_time": ad.get("total_active_time"),
        "collation_count": ad.get("collation_count"),
        "categories": ad.get("categories") or [],
        # --- usually null for commercial ads; populated for EU / political --- #
        "reach_estimate": ad.get("reach_estimate"),
        "spend": ad.get("spend"),
        "currency": ad.get("currency") or None,
        "impressions_text": (ad.get("impressions_with_index") or {}).get(
            "impressions_text"
        ),
        # --- signals godofroas.com does not surface -------------------------- #
        "video_duration_s": efg.get("duration_s"),
        "creative_asset_age_days": efg.get("asset_age_days"),
        "creative_asset_id": efg.get("xpv_asset_id"),
        "source_url": source_url,
    }


from app.scoring import score_ad

# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


class ExtractIn(BaseModel):
    url: str
    country: str = DEFAULT_COUNTRY


@app.post("/api/extract")
async def extract(payload: ExtractIn, request: Request) -> dict:
    rate_limit(request)
    ad_id = extract_ad_id(payload.url)

    # 1. Check in-memory cache
    cached = _extract_cache.get(ad_id)
    if cached is not None:
        return cached

    target = ad_library_url(ad_id, payload.country)

    session = await get_session()
    try:
        resp = await fetch_with_challenge(session, target)
    except Exception as exc:
        raise HTTPException(502, f"Could not reach Meta: {exc}") from exc

    # A stale session can start failing. Rebuild once and retry.
    if resp.status_code != 200 or "deeplink_ad_archive" not in resp.text:
        session = await get_session(force_new=True)
        try:
            resp = await fetch_with_challenge(session, target)
        except Exception as exc:
            raise HTTPException(502, f"Could not reach Meta: {exc}") from exc

    if resp.status_code != 200:
        raise HTTPException(502, f"Meta returned HTTP {resp.status_code}")

    ad_data = normalise(find_ad_object(resp.text, ad_id), ad_id, target)

    try:
        score_obj = score_ad(ad_data)
    except Exception as exc:
        print(f"[ERROR] Scoring failed for ad_id={ad_id}: {exc}")
        score_obj = None

    ad_data["score"] = score_obj

    # 2. Store in cache only on successful extraction
    now = time.time()
    ad_data["cache_hit"] = False
    ad_data["cached_at"] = now
    _extract_cache.set(ad_id, ad_data)

    return ad_data


@app.post("/api/score")
async def score_endpoint(payload: dict, request: Request) -> dict:
    rate_limit(request)
    try:
        return score_ad(payload)
    except Exception as exc:
        raise HTTPException(400, f"Could not score ad payload: {exc}") from exc


class CreateOrderIn(BaseModel):
    amount: int


@app.post("/api/create-order")
async def create_order(payload: CreateOrderIn, request: Request) -> dict:
    rate_limit(request)

    if payload.amount not in ALLOWED_PAYMENT_AMOUNTS:
        raise HTTPException(
            400, f"Invalid amount. Amount must be one of: {sorted(list(ALLOWED_PAYMENT_AMOUNTS))}"
        )

    key_id = os.getenv("RAZORPAY_KEY_ID") or RAZORPAY_KEY_ID
    key_secret = os.getenv("RAZORPAY_KEY_SECRET") or RAZORPAY_KEY_SECRET

    if not key_id or not key_secret:
        print("[ERROR] Razorpay credentials not set in environment.")
        raise HTTPException(500, "Payment service authentication is not configured.")

    client = razorpay.Client(auth=(key_id, key_secret))
    receipt_id = f"rcpt_{uuid.uuid4().hex[:12]}"

    try:
        order = client.order.create(
            data={
                "amount": payload.amount,
                "currency": "INR",
                "receipt": receipt_id,
                "payment_capture": 1,
            }
        )
    except razorpay.errors.AuthenticationError as exc:
        print(f"[ERROR] Razorpay auth failure: {exc}")
        raise HTTPException(
            500, "Razorpay authentication failed. Check credentials."
        ) from exc
    except Exception as exc:
        print(f"[ERROR] Razorpay order creation failed: {exc}")
        raise HTTPException(502, f"Razorpay API error: {exc}") from exc

    order_id = order.get("id") if isinstance(order, dict) else None
    if not order_id:
        raise HTTPException(502, "Razorpay did not return a valid order ID.")

    return {
        "order_id": order_id,
        "amount": payload.amount,
        "currency": "INR",
        "key_id": key_id,
    }


class VerifyPaymentIn(BaseModel):
    razorpay_order_id: str = ""
    razorpay_payment_id: str = ""
    razorpay_signature: str = ""


@app.post("/api/verify-payment")
async def verify_payment(payload: VerifyPaymentIn, request: Request):
    rate_limit(request)

    if (
        not payload.razorpay_order_id
        or not payload.razorpay_payment_id
        or not payload.razorpay_signature
    ):
        raise HTTPException(400, "Missing required Razorpay payment fields.")

    key_secret = os.getenv("RAZORPAY_KEY_SECRET") or RAZORPAY_KEY_SECRET
    if not key_secret:
        print("[ERROR] Razorpay secret not set in environment.")
        raise HTTPException(500, "Payment service authentication is not configured.")

    msg = f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}".encode("utf-8")
    generated_signature = hmac.new(
        key_secret.encode("utf-8"), msg, hashlib.sha256
    ).hexdigest()

    if hmac.compare_digest(generated_signature, payload.razorpay_signature):
        print(
            f"[PAID] Verified payment {payload.razorpay_payment_id} for order {payload.razorpay_order_id}"
        )
        return {"verified": True}

    print(
        f"[WARNING] Invalid payment signature attempt for order_id={payload.razorpay_order_id}, payment_id={payload.razorpay_payment_id}"
    )
    return JSONResponse(status_code=400, content={"verified": False})


@app.get("/api/proxy")
async def proxy(url: str, request: Request, filename: str = "ad.mp4"):
    """Stream fbcdn media back as a browser attachment."""
    rate_limit(request)

    host = (urlparse(url).hostname or "").lower()
    if not host.endswith(ALLOWED_MEDIA_SUFFIXES):
        raise HTTPException(400, "Only Meta CDN URLs may be proxied.")

    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:80] or "ad.mp4"
    session = AsyncSession(impersonate=IMPERSONATE, timeout=30)

    try:
        req_ctx = session.stream("GET", url, headers={"Referer": "https://www.facebook.com/"})
        resp = await req_ctx.__aenter__()
    except Exception as exc:
        try:
            await session.close()
        except Exception:
            pass
        raise HTTPException(502, f"Could not reach Meta CDN: {exc}") from exc

    status = resp.status_code
    cl_header = resp.headers.get("content-length")
    content_length = int(cl_header) if cl_header and cl_header.isdigit() else None

    # Upstream non-200 or 200 with Content-Length == 0 indicates expired or missing link
    if status in (403, 404, 410) or (status == 200 and content_length == 0):
        try:
            await req_ctx.__aexit__(None, None, None)
            await session.close()
        except Exception:
            pass
        raise HTTPException(
            410,
            "This download link has expired. Re-run the extract to get a fresh one."
        )

    if status != 200:
        try:
            await req_ctx.__aexit__(None, None, None)
            await session.close()
        except Exception:
            pass
        raise HTTPException(502, f"Meta media CDN returned HTTP {status}.")

    async def body():
        try:
            async for chunk in resp.aiter_content():
                yield chunk
        finally:
            try:
                await req_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await session.close()
            except Exception:
                pass

    media_type = "video/mp4" if filename.endswith(".mp4") else "application/octet-stream"
    resp_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    }
    if content_length is not None and content_length > 0:
        resp_headers["Content-Length"] = str(content_length)

    return StreamingResponse(
        body(),
        media_type=media_type,
        headers=resp_headers,
    )


@app.get("/api/health")
async def health() -> dict:
    res = {
        "ok": True,
        "session_age_s": round(time.time() - _session_born),
    }
    res.update(_extract_cache.stats)
    return res


# --------------------------------------------------------------------------- #
# Offline parse check
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "ad_page.html"
    with open(path, encoding="utf-8") as fh:
        page = fh.read()

    result = normalise(find_ad_object(page), "", path)
    print(f"\n  Parsed {path} ({len(page):,} chars)\n" + "-" * 62)
    for key, value in result.items():
        if key == "source_url":
            continue
        shown = str(value)
        print(f"  {key:<26} {shown[:70] + ('...' if len(shown) > 70 else '')}")
    missing = [k for k, v in result.items() if v in (None, [], "")]
    print("-" * 62)
    print(f"  empty fields: {', '.join(missing) if missing else 'none'}\n")
