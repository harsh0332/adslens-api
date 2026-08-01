import time
from app.main import ExtractCache

def test_cache_hit_and_miss():
    cache = ExtractCache(max_size=5, ttl_s=1800)
    
    # 1. Initial miss
    assert cache.get("123") is None
    assert cache.hits == 0
    assert cache.misses == 1

    # 2. Store payload
    payload = {"ad_id": "123", "page_name": "TestPage"}
    cache.set("123", payload)

    # 3. Cache hit
    cached = cache.get("123")
    assert cached is not None
    assert cached["ad_id"] == "123"
    assert cached["cache_hit"] is True
    assert "cached_at" in cached
    assert cache.hits == 1
    assert cache.misses == 1

def test_cache_ttl_expiration():
    cache = ExtractCache(max_size=5, ttl_s=10)
    cache.set("456", {"ad_id": "456"})
    
    # Simulate time past TTL
    entry = cache._cache["456"]
    entry["cached_at"] = time.time() - 20

    assert cache.get("456") is None
    assert cache.misses == 1

def test_cache_lru_eviction():
    cache = ExtractCache(max_size=3, ttl_s=1800)
    cache.set("ad1", {"ad_id": "ad1"})
    cache.set("ad2", {"ad_id": "ad2"})
    cache.set("ad3", {"ad_id": "ad3"})
    
    # Access ad1 so ad2 becomes LRU
    cache.get("ad1")

    # Add ad4 (capacity exceeded, should evict ad2)
    cache.set("ad4", {"ad_id": "ad4"})

    assert cache.get("ad2") is None  # evicted
    assert cache.get("ad1") is not None
    assert cache.get("ad3") is not None
    assert cache.get("ad4") is not None

def test_cache_stats():
    cache = ExtractCache(max_size=10, ttl_s=1800)
    cache.set("a", {"ad_id": "a"})
    cache.get("a")  # hit
    cache.get("b")  # miss

    stats = cache.stats
    assert stats["cache_size"] == 1
    assert stats["cache_hits"] == 1
    assert stats["cache_misses"] == 1
    assert stats["cache_hit_rate"] == 0.5
