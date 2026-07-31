from pathlib import Path
import pytest
from fastapi import HTTPException
from app.main import find_ad_object, normalise

FIXTURES_DIR = Path(__file__).parent / "fixtures"

def load_fixture(filename: str) -> str:
    path = FIXTURES_DIR / filename
    assert path.exists(), f"Fixture {filename} missing"
    with open(path, encoding="utf-8") as f:
        return f.read()

def test_parse_video_ad():
    html = load_fixture("ad_1589815832849858.html")
    obj = find_ad_object(html, "1589815832849858")
    norm = normalise(obj, "1589815832849858", "https://www.facebook.com/ads/library/?id=1589815832849858")
    
    assert norm["ad_id"] == "1589815832849858"
    assert norm["page_name"] == "Lenskart"
    assert norm["display_format"] == "VIDEO"
    assert norm["video_hd_url"] is not None
    assert norm["video_duration_s"] == 24
    assert norm["is_active"] is True
    assert "Artist" in norm["page_categories"]
    assert norm["page_like_count"] == 144222

def test_parse_single_image_ad():
    html = load_fixture("ad_1105780848315170.html")
    obj = find_ad_object(html, "1105780848315170")
    norm = normalise(obj, "1105780848315170", "https://www.facebook.com/ads/library/?id=1105780848315170")
    
    assert norm["ad_id"] == "1105780848315170"
    assert norm["display_format"] == "IMAGE"
    assert norm["image_url"] is not None
    assert norm["video_hd_url"] is None
    assert len(norm["cards"]) == 0

def test_parse_carousel_ad():
    html = load_fixture("ad_3050558185275896.html")
    obj = find_ad_object(html, "3050558185275896")
    norm = normalise(obj, "3050558185275896", "https://www.facebook.com/ads/library/?id=3050558185275896")
    
    assert norm["ad_id"] == "3050558185275896"
    assert len(norm["cards"]) == 6
    assert norm["cards"][0]["title"] is not None
    assert norm["cards"][0]["image_url"] is not None

def test_parse_dco_ad():
    html = load_fixture("ad_1457997729405444.html")
    obj = find_ad_object(html, "1457997729405444")
    norm = normalise(obj, "1457997729405444", "https://www.facebook.com/ads/library/?id=1457997729405444")
    
    assert norm["ad_id"] == "1457997729405444"
    assert norm["video_count"] == 2

def test_parse_inactive_ad():
    html = load_fixture("ad_1530717628759192.html")
    obj = find_ad_object(html, "1530717628759192")
    norm = normalise(obj, "1530717628759192", "https://www.facebook.com/ads/library/?id=1530717628759192")
    
    assert norm["ad_id"] == "1530717628759192"
    assert norm["is_active"] is False

def test_non_existent_ad_id_raises_502():
    html = load_fixture("ad_1589815832849858.html")
    with pytest.raises(HTTPException) as exc_info:
        find_ad_object(html, "999999999999999")
    assert exc_info.value.status_code == 502
    assert "999999999999999" in str(exc_info.value.detail)
