# AdSpy — Meta Ad Library extractor

Paste a Meta Ad Library link, get the ad's data and creative back as clean JSON,
and download the video. Backend only for now; the frontend comes later.

## Quick start

```bash
./run.sh
```

Then:

```bash
curl -s -X POST http://localhost:8000/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.facebook.com/ads/library/?id=1277136211266420"}'
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/extract` | `{"url": "..."}` → normalised ad JSON |
| GET | `/api/proxy` | `?url=<fbcdn>&filename=x.mp4` → streamed attachment |
| GET | `/api/health` | liveness + session age |

## Offline parser check

No network needed — parses a saved page:

```bash
python3 -m app.main ad_page.html
```

## How it works

1. `curl_cffi` presents a real Chrome TLS fingerprint. Plain Python HTTP clients
   get `403` from Meta no matter what headers they send.
2. Meta's first response is a `403` carrying a tiny JS challenge. We replay that
   handshake over plain HTTP; Meta then sets an `rd_challenge` cookie.
3. One shared session holds that cookie, so the challenge is solved about once
   per process rather than once per user.
4. The parser locates the ad object by SHAPE (a dict with a `snapshot` child)
   rather than by a fixed JSON path, because Meta renames wrappers regularly.
5. `/api/proxy` streams the fbcdn file through with
   `Content-Disposition: attachment`. Nothing is written to disk.

Full detail, including Meta's verified JSON structure and the traps in it, is in
[`AGENT_TASK.md`](AGENT_TASK.md).

## Notable fields

Beyond the obvious copy and media fields:

| Field | Why it matters |
|---|---|
| `video_duration_s` | Decoded from the fbcdn `efg` param |
| `creative_asset_age_days` | Asset older than the ad means it was validated elsewhere first |
| `page_like_count` | Advertiser size |
| `page_categories` | Advertiser vertical — needed for per-industry benchmarks |
| `collation_count` | Number of variants of the same creative, when Meta publishes it |
| `cta_type` | Machine-readable CTA, e.g. `SHOP_NOW` |

## Fields that are usually null

`spend`, `reach_estimate`, `currency`, `impressions_text`, `total_active_time`,
`collation_count`. Meta only publishes these for EU and political/issue ads.
Null here is correct behaviour, not a parsing failure.

## Constraints worth keeping

- Rate limited per IP.
- `/api/proxy` only accepts Meta CDN hosts. Without that check it is an open
  proxy for anyone on the internet.
- No media is ever stored server-side.
- fbcdn URLs are signed and expire within hours. Cache extracted metadata if you
  like, never the media URL.

## Legal note

Intended for private creative research and swipe-file building from Meta's
public Ad Library. Reusing a competitor's creative in your own advertising is a
copyright matter. Ship a disclaimer with any public deployment.
