from app.main import decode_efg

def test_decode_efg_valid_url():
    url = (
        "https://video.frpr1-1.fna.fbcdn.net/o1/v/t2/f2/m366/AQMDiJ0.mp4?"
        "efg=eyJ2ZW5jb2RlX3RhZyI6Inhwdl9wcm9ncmVzc2l2ZS5WSV9VU0VDQVNFX1BST0RVQ1RfVFlQRS4uQzMuNzIwLmRhc2hf"
        "aDI2NC1iYXNpYy1nZW4yXzcyMHAiLCJ4cHZfYXNzZXRfaWQiOjE1MjE1NjMyODYzNTU5NDksImFzc2V0X2FnZV9kYXlzIjox"
        "NDAsInZpX3VzZWNhc2VfaWQiOjEwNzk5LCJkdXJhdGlvbl9zIjozMSwidXJsZ2VuX3NvdXJjZSI6Ind3dyJ9"
    )
    data = decode_efg(url)
    assert data.get("duration_s") == 31
    assert data.get("asset_age_days") == 140
    assert data.get("xpv_asset_id") == 1521563286355949

def test_decode_efg_no_efg_param():
    url = "https://video.frpr1-1.fna.fbcdn.net/video.mp4?oh=12345&oe=67890"
    assert decode_efg(url) == {}

def test_decode_efg_none_or_empty():
    assert decode_efg(None) == {}
    assert decode_efg("") == {}

def test_decode_efg_malformed_b64():
    url = "https://video.frpr1-1.fna.fbcdn.net/video.mp4?efg=!!!not_valid_b64!!!"
    assert decode_efg(url) == {}
