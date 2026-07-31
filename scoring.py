"""
Ad scoring — one score, deterministic, no AI, no network.

Design notes, and why this differs from godofroas.com:

  * ONE score. They show a /100 panel and a /10 panel that disagree — the same
    ad scored 55/100 and 10/10 simultaneously. A user cannot act on that.

  * NO CEILING BUG. Their /100 formula is effectively `is_active x age`, so any
    campaign under 90 days that is not multi-country is capped at 55 no matter
    how good the creative is. Ours splits market evidence (Proof, 0-60) from
    creative quality (Craft, 0-40), so a well-built 3-week ad can still score in
    the 60s.

  * SIGNALS THEY THROW AWAY. video_duration_s and creative_asset_age_days are
    decoded out of the fbcdn `efg` param; page_like_count and page_categories
    come from the snapshot. An asset older than the ad it runs in means the
    creative was validated somewhere else first — the single most useful signal
    available, and nobody uses it.

  * ACTIONS, NOT A NUMBER. `steal` returns concrete things to copy. A score
    tells you an ad is good; it does not tell you what to do on Monday.

Honest limits: Meta does not publish spend, impressions, CTR or ROAS for
commercial ads. Everything here is inference from public delivery metadata.
`confidence` reports how much of the input was actually present so the UI can
say so out loud rather than implying certainty we do not have.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

# Bottom-funnel CTAs signal a closing ad; soft CTAs signal awareness.
STRONG_CTAS = {
    "SHOP_NOW", "BUY_NOW", "ORDER_NOW", "GET_OFFER", "SIGN_UP", "SUBSCRIBE",
    "BOOK_TRAVEL", "BOOK_NOW", "GET_QUOTE", "APPLY_NOW", "DOWNLOAD",
    "INSTALL_MOBILE_APP", "SEND_MESSAGE", "WHATSAPP_MESSAGE", "CALL_NOW",
}
SOFT_CTAS = {"LEARN_MORE", "SEE_MORE", "WATCH_MORE", "CONTACT_US", "NO_BUTTON"}

HOOK_SIGNALS = re.compile(r"[0-9]|%|₹|\$|\?|free|new|only|save", re.IGNORECASE)
STOPWORDS = {
    "the", "and", "for", "you", "your", "with", "from", "that", "this", "are",
    "our", "get", "all", "can", "now", "out", "was", "has", "have", "will",
    "just", "but", "not", "who", "why", "how", "what", "when", "into", "more",
}


def _words(text: str | None) -> set[str]:
    if not text:
        return set()
    return {
        w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOPWORDS
    }


def _days_since(ts: int | float | None, now: float | None = None) -> int | None:
    if not ts:
        return None
    now = now or time.time()
    return max(0, int((now - float(ts)) / 86400))


# --------------------------------------------------------------------------- #
# Proof — market evidence, 0-60
# --------------------------------------------------------------------------- #


def _proof(ad: dict, days: int | None) -> tuple[int, list[dict]]:
    points = 0
    notes: list[dict] = []

    if ad.get("is_active") is True:
        points += 12
        notes.append({"ok": True, "text": "Still running today"})
    elif ad.get("is_active") is False:
        notes.append({"ok": False, "text": "Ad has stopped running"})

    # Graduated, not a cliff. An ad at 29 days should not score the same as one
    # at 8 days just because both sit under a 30-day threshold.
    if days is not None:
        for threshold, award, label in (
            (180, 28, "running over 6 months — evergreen"),
            (90, 25, "running over 3 months — proven"),
            (60, 21, "running over 2 months"),
            (30, 16, "running over a month"),
            (14, 10, "past the two-week test window"),
            (7, 5, "past the first week"),
            (0, 0, "still inside the test window"),
        ):
            if days >= threshold:
                points += award
                notes.append(
                    {"ok": award >= 10, "text": f"{days} days live — {label}"}
                )
                break

    platforms = ad.get("publisher_platforms") or []
    spread = {1: 0, 2: 2, 3: 4, 4: 5}.get(len(platforms), 6 if platforms else 0)
    points += spread
    if len(platforms) >= 4:
        notes.append(
            {"ok": True, "text": f"Delivering on {len(platforms)} placements — "
                                 "broad budget, likely Advantage+"}
        )

    # An asset older than the ad means the creative earned its place elsewhere
    # before this campaign. Strongest signal available, and unused by rivals.
    # Only ~27% of ads expose this (Meta omits the efg param on most non-video
    # creatives). Absence is missing evidence, not evidence of weakness, so an
    # unknown asset age scores the same as a known-but-fresh one. Scoring 0 here
    # silently cost three quarters of all ads 8 points they could never earn.
    asset_age = ad.get("creative_asset_age_days")
    if asset_age and days is not None and asset_age > days + 14:
        points += 8
        notes.append(
            {"ok": True, "text": f"Creative is {asset_age} days old but this "
                                 f"ad is {days} — a recycled proven asset"}
        )
    else:
        points += 3

    if (ad.get("collation_count") or 0) > 1:
        points += 6
        notes.append(
            {"ok": True, "text": f"{ad['collation_count']} variants of this "
                                 "creative in market — being scaled"}
        )

    return min(points, 60), notes


# --------------------------------------------------------------------------- #
# Craft — creative quality, 0-40
# --------------------------------------------------------------------------- #


def _craft(ad: dict) -> tuple[int, list[dict], list[str]]:
    points = 0
    notes: list[dict] = []
    warnings: list[str] = []

    body = ad.get("body") or ""
    title = ad.get("title") or ""

    length = len(body)
    if 80 <= length <= 300:
        points += 8
        notes.append({"ok": True, "text": f"Body copy {length} chars — in the "
                                           "80-300 range that converts on Meta"})
    elif 40 <= length < 80 or 300 < length <= 500:
        points += 4
    elif length == 0:
        warnings.append("No body copy — the creative is carrying the whole sell")
    elif length < 40:
        warnings.append(f"Body copy only {length} chars — thin on context")
    else:
        warnings.append(f"Body copy {length} chars — attention drops past 300")

    if body and HOOK_SIGNALS.search(body[:60]):
        points += 5
        notes.append({"ok": True, "text": "Opens with a number, price or "
                                           "question — a real hook"})
    elif body:
        warnings.append("Opening line has no number, price or question to grab on")

    if title:
        points += 4
        shared = _words(title) & _words(body)
        if len(shared) >= 2:
            points += 3
            notes.append(
                {"ok": True, "text": "Headline and body reinforce each other: "
                                     + ", ".join(sorted(shared)[:3])}
            )
    else:
        warnings.append("No headline — leaving conversion signal on the table")

    cta = (ad.get("cta_type") or "").upper()
    if cta in STRONG_CTAS:
        points += 5
        notes.append({"ok": True, "text": f"'{ad.get('cta_text')}' is a closing "
                                           "CTA — bottom of funnel"})
    elif cta in SOFT_CTAS:
        points += 2
        warnings.append(f"'{ad.get('cta_text')}' is a soft CTA — awareness, not sale")
    elif cta:
        points += 3

    fmt = (ad.get("display_format") or "").upper()
    duration = ad.get("video_duration_s")
    cards = ad.get("cards") or []

    # Format fitness. Duration is only published for about a quarter of ads, so
    # a video whose duration Meta withheld must not be punished against an image
    # ad — an earlier version scored it 0 here and pushed VIDEO below IMAGE in
    # the averages, which is backwards.
    multi_asset_formats = {"DCO", "DPA", "CAROUSEL", "AUTOMATED_ANIMATION"}

    if duration:
        if 6 <= duration <= 45:
            points += 6
            notes.append({"ok": True, "text": f"{duration}s video — the range "
                                              "that holds attention"})
        elif 46 <= duration <= 90:
            points += 3
            warnings.append(f"{duration}s video — long for cold traffic")
        else:
            points += 1
            warnings.append(f"{duration}s video — outside the usual range")
    elif len(cards) >= 3:
        points += 5
        notes.append({"ok": True, "text": f"{len(cards)}-card carousel — letting "
                                          "Meta pick the winner"})
    elif fmt in multi_asset_formats:
        points += 5
        notes.append({"ok": True, "text": f"{fmt} ad — Meta is assembling the "
                                          "creative, a sign of a mature setup"})
    elif fmt in ("VIDEO", "IMAGE"):
        points += 3
    else:
        points += 2

    link = ad.get("link_url") or ""
    if link:
        points += 3
        parsed = urlparse(link)
        deep = len(parsed.path.strip("/")) > 0
        tracked = "utm_" in (parsed.query or "").lower()
        if deep or tracked:
            points += 2
            if tracked:
                notes.append({"ok": True, "text": "Landing page carries UTM "
                                                  "tracking — mature setup"})
        else:
            warnings.append("Sends traffic to a bare homepage, not a product page")
    else:
        warnings.append("No landing page link found")

    assets = (ad.get("video_count") or 0) + (ad.get("image_count") or 0) + len(cards)
    if assets >= 3:
        points += 4
        notes.append({"ok": True, "text": f"{assets} creative assets in one ad — "
                                          "they are testing variations"})

    return min(points, 40), notes, warnings


# --------------------------------------------------------------------------- #
# What to actually do with this ad
# --------------------------------------------------------------------------- #


def _steal(ad: dict, days: int | None) -> list[str]:
    out: list[str] = []
    body = ad.get("body") or ""

    if body:
        hook = body.split(".")[0][:90]
        out.append(f'Hook to model: "{hook}"')

    if ad.get("creative_asset_age_days") and days is not None:
        if ad["creative_asset_age_days"] > days + 14:
            out.append(
                "This creative predates the campaign — search the same page in "
                "Ad Library for older ads using it, that is their real winner"
            )

    if ad.get("video_duration_s"):
        out.append(f"Match the {ad['video_duration_s']}s runtime — they have "
                   "already paid to find that length")

    if len(ad.get("cards") or []) >= 3:
        out.append(f"Build {len(ad['cards'])} creative variants and let Meta "
                   "allocate, the way they are")

    cta = (ad.get("cta_type") or "").upper()
    if cta in STRONG_CTAS:
        out.append(f"They use '{ad.get('cta_text')}' — closing straight from the "
                   "feed, no warm-up step")

    platforms = ad.get("publisher_platforms") or []
    if len(platforms) >= 4:
        out.append("Placements are wide open, not hand-picked — budget is going "
                   "through Advantage+")

    likes = ad.get("page_like_count")
    if likes and likes > 100_000:
        out.append(f"Page has {likes:,} followers — brand recall is doing part "
                   "of the work, expect weaker numbers cold")

    return out


def _stage(days: int | None) -> str:
    if days is None:
        return "Unknown"
    if days <= 7:
        return "Testing"
    if days <= 21:
        return "Validation"
    if days <= 60:
        return "Scaling"
    if days <= 180:
        return "Winning"
    return "Evergreen"


def _verdict(score: int) -> tuple[str, str]:
    if score >= 80:
        return "Proven Winner", "green"
    if score >= 65:
        return "Strong Performer", "green"
    if score >= 45:
        return "Working", "amber"
    if score >= 30:
        return "Early Signal", "amber"
    return "Unproven", "red"


def score_ad(ad: dict, now: float | None = None) -> dict[str, Any]:
    """Score a normalised ad dict. Pure function — no network, no clock surprises."""
    days = _days_since(ad.get("start_date_ts"), now)

    proof, proof_notes = _proof(ad, days)
    craft, craft_notes, warnings = _craft(ad)
    total = proof + craft
    verdict, tone = _verdict(total)

    # Confidence reflects how much of the input was actually present, so the UI
    # can be honest instead of implying precision we do not have.
    present = sum(
        1 for key in (
            "start_date_ts", "is_active", "publisher_platforms", "body",
            "display_format", "link_url",
        ) if ad.get(key) not in (None, "", [])
    )
    confidence = "High" if present >= 6 else "Medium" if present >= 4 else "Low"

    return {
        "score": total,
        "verdict": verdict,
        "tone": tone,
        "proof_score": proof,
        "craft_score": craft,
        "days_running": days,
        "stage": _stage(days),
        "confidence": confidence,
        "why_it_works": proof_notes + craft_notes,
        "watch_outs": warnings,
        "steal": _steal(ad, days),
        "disclaimer": (
            "Meta does not publish spend, impressions or ROAS for commercial "
            "ads. This score is inferred from public delivery data only."
        ),
    }
