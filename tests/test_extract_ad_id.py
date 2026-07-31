import pytest
from fastapi import HTTPException
from app.main import extract_ad_id

def test_extract_ad_id_bare_id():
    assert extract_ad_id("1277136211266420") == "1277136211266420"
    assert extract_ad_id("  1277136211266420  ") == "1277136211266420"

def test_extract_ad_id_full_url():
    url = "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL&id=1277136211266420&search_type=keyword_unordered"
    assert extract_ad_id(url) == "1277136211266420"

def test_extract_ad_id_url_with_extra_params():
    url = "https://www.facebook.com/ads/library/?id=1277136211266420&utm_source=test&ref=share"
    assert extract_ad_id(url) == "1277136211266420"

def test_extract_ad_id_ad_archive_id_param():
    url = "https://www.facebook.com/ads/library/?ad_archive_id=1277136211266420"
    assert extract_ad_id(url) == "1277136211266420"

def test_extract_ad_id_embedded_id():
    url = "https://www.facebook.com/ads/library/1277136211266420/view"
    assert extract_ad_id(url) == "1277136211266420"

def test_extract_ad_id_garbage():
    with pytest.raises(HTTPException) as exc_info:
        extract_ad_id("https://example.com/no_ad_id_here")
    assert exc_info.value.status_code == 400
    assert "Could not find an ad id" in str(exc_info.value.detail)

def test_extract_ad_id_invalid_string():
    with pytest.raises(HTTPException) as exc_info:
        extract_ad_id("random_text_without_digits")
    assert exc_info.value.status_code == 400
