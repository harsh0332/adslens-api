from urllib.parse import urlparse
import pytest
from fastapi import HTTPException
from app.main import ALLOWED_MEDIA_SUFFIXES

def is_allowed_media_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith(ALLOWED_MEDIA_SUFFIXES)

def test_proxy_host_allowlist_allowed_urls():
    allowed_urls = [
        "https://video.frpr1-1.fna.fbcdn.net/v/t2/m366/video.mp4?oh=123",
        "https://scontent.frpr1-2.fna.fbcdn.net/v/t39.35426-6/image.jpg",
        "https://scontent.cdninstagram.com/v/t51.2885-15/image.jpg",
        "https://subdomain.scontent.cdninstagram.com/media.mp4"
    ]
    for url in allowed_urls:
        assert is_allowed_media_url(url) is True, f"URL {url} should be allowed"

def test_proxy_host_allowlist_rejected_urls():
    rejected_urls = [
        "https://example.com/video.mp4",
        "https://facebook.com/ads/library",
        "https://malicious-fbcdn.net.attacker.com/video.mp4",
        "https://fbcdn.net.evildomain.org/file.mp4",
        "http://localhost:8000/api/health",
        "https://google.com/"
    ]
    for url in rejected_urls:
        assert is_allowed_media_url(url) is False, f"URL {url} should be rejected"
