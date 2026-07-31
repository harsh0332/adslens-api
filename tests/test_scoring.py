import time
import pytest
from app.scoring import score_ad

def test_high_quality_old_ad():
    now = 1750000000.0
    ad = {
        "ad_id": "1001",
        "page_name": "Pro Brand",
        "page_like_count": 500000,
        "is_active": True,
        "start_date_ts": int(now - 120 * 86400), # 120 days old
        "publisher_platforms": ["FACEBOOK", "INSTAGRAM", "AUDIENCE_NETWORK", "MESSENGER"],
        "creative_asset_age_days": 180,
        "collation_count": 3,
        "body": "Get 20% off your first purchase! Save on 100s of top-quality items today.",
        "title": "Save Big on First Purchase",
        "cta_type": "SHOP_NOW",
        "cta_text": "Shop Now",
        "display_format": "VIDEO",
        "video_duration_s": 30,
        "link_url": "https://example.com/product?utm_source=fb&utm_medium=cpc",
        "video_count": 2,
        "image_count": 1,
        "cards": []
    }
    res = score_ad(ad, now=now)
    assert res["score"] >= 75
    assert res["proof_score"] + res["craft_score"] == res["score"]
    assert 0 <= res["score"] <= 100

def test_thin_brand_new_ad():
    now = 1750000000.0
    ad = {
        "ad_id": "1002",
        "page_name": "Newbie Store",
        "is_active": True,
        "start_date_ts": int(now - 2 * 86400), # 2 days old
        "publisher_platforms": ["FACEBOOK"],
        "body": "Check this out",
        "title": None,
        "cta_type": "LEARN_MORE",
        "cta_text": "Learn More",
        "display_format": "IMAGE",
        "video_duration_s": None,
        "link_url": "https://example.com",
        "cards": []
    }
    res = score_ad(ad, now=now)
    assert res["score"] <= 30
    assert res["proof_score"] + res["craft_score"] == res["score"]
    assert 0 <= res["score"] <= 100

def test_strong_24_day_old_ad_rival_ceiling_bug():
    """
    Rival tool caps any ad under 90 days at 55 regardless of creative quality.
    Our engine splits Proof from Craft so a strong 24-day-old ad scores >= 65.
    """
    now = 1750000000.0
    ad = {
        "ad_id": "1003",
        "page_name": "Fast Scaler",
        "page_like_count": 250000,
        "is_active": True,
        "start_date_ts": int(now - 24 * 86400), # 24 days old
        "publisher_platforms": ["FACEBOOK", "INSTAGRAM", "AUDIENCE_NETWORK", "MESSENGER"],
        "creative_asset_age_days": 60, # recycled asset
        "collation_count": 2,
        "body": "Save 30% today! Over 500+ five-star reviews from happy customers.",
        "title": "Save 30% Today",
        "cta_type": "SHOP_NOW",
        "cta_text": "Shop now",
        "display_format": "VIDEO",
        "video_duration_s": 25,
        "link_url": "https://example.com/item?utm_source=meta",
        "video_count": 3,
        "cards": []
    }
    res = score_ad(ad, now=now)
    assert res["score"] >= 65, f"Expected score >= 65 for strong 24-day-old ad, got {res['score']}"
    assert res["days_running"] == 24
    assert res["proof_score"] + res["craft_score"] == res["score"]

def test_missing_start_date_ts():
    ad = {
        "ad_id": "1004",
        "is_active": True,
        "publisher_platforms": ["FACEBOOK"],
        "body": "Some ad body copy text",
        "display_format": "IMAGE"
    }
    res = score_ad(ad)
    assert res["days_running"] is None
    assert res["confidence"] != "High"
    assert res["proof_score"] + res["craft_score"] == res["score"]

def test_minimal_ad_dict_body_only():
    ad = {"body": "x"}
    res = score_ad(ad)
    assert isinstance(res["score"], int)
    assert 0 <= res["score"] <= 100
    assert res["proof_score"] + res["craft_score"] == res["score"]

def test_score_is_int_and_range():
    ad = {
        "is_active": True,
        "start_date_ts": 1700000000,
        "publisher_platforms": ["FACEBOOK", "INSTAGRAM"],
        "body": "Special offer: Save $50 on your first order!",
        "title": "Save $50 Now",
        "cta_type": "BUY_NOW",
        "cta_text": "Buy Now",
        "display_format": "VIDEO",
        "video_duration_s": 20
    }
    res = score_ad(ad)
    assert isinstance(res["score"], int)
    assert 0 <= res["score"] <= 100
    assert res["proof_score"] + res["craft_score"] == res["score"]
