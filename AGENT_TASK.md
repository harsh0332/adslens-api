# AGENT TASK — AdSpy backend hardening

You are working on a FastAPI backend that extracts public ad data from the Meta
Ad Library and streams the creative back to the user as a download.

**Read this entire file before writing any code.** It contains findings from a
prior debugging session. Several of them are non-obvious and cost hours to
discover. Re-deriving them wastes your time; deleting them breaks the app.

---

## 1. What already works — DO NOT REGRESS THESE

The backend in `app/main.py` is verified working end to end. Three mechanisms
are load-bearing:

### 1.1 curl_cffi, not httpx / requests
Meta fingerprints the TLS handshake. Any plain Python HTTP client gets `403`
regardless of how perfect its headers are. `curl_cffi` with
`impersonate="chrome"` presents a real Chrome handshake and gets through.

**Do not** swap this back to httpx, requests, or aiohttp "for consistency."

### 1.2 The JS challenge handshake
Meta answers the first Ad Library request with `403` and a ~481 byte HTML body:

```html
<script>
  fetch('/__rd_verify_<TOKEN>?challenge=3', { method: 'POST' })
    .finally(() => window.location.reload());
</script>
```

This is a mechanical handshake, not a computation. We replay it over plain HTTP:
extract the path, POST to it on the same session, re-request the page. Meta then
sets an `rd_challenge` cookie and serves real data.

This is implemented in `fetch_with_challenge()`. **Do not remove it**, and do
not "simplify" it into a single request.

### 1.3 One shared, long-lived session
The `rd_challenge` cookie survives on the session, so the challenge is solved
roughly once per process rather than once per user. `get_session()` handles
creation, reuse, and 30-minute recycling.

**Do not** create a new `AsyncSession` per request in `/api/extract`. That would
hammer Meta and get the server IP blocked.

---

## 2. Meta's real JSON shape — verified, do not guess

The ad payload is embedded in `<script type="application/json">` blocks. The ad
object sits at:

```
$.require[0][3][0].__bbox.require[0][3][1].__bbox.result.data
  .ad_library_main.deeplink_ad_archive_result.deeplink_ad_archive
```

**Do not hard-code that path.** Meta renames wrappers between releases. The
parser finds it by shape instead: the dict that has a `snapshot` dict child, and
whose `ad_archive_id` matches the requested id. Keep it that way.

### Ad object (outer level)
```
ad_archive_id, ad_id, categories, collation_count, collation_id,
contains_digital_created_media, contains_sensitive_content, currency,
end_date, fev_info, gated_type, has_user_reported, hide_data_status,
impressions_with_index, is_aaa_eligible, is_active, menu_items, page_id,
page_is_deleted, page_name, publisher_platform, reach_estimate,
regional_regulation_data, report_count, snapshot, spend, start_date,
state_media_run_label, total_active_time
```

### snapshot (inner level)
```
additional_info, body, branded_content, brazil_tax_id, byline, caption, cards,
country_iso_code, cta_text, cta_type, disclaimer_label, display_format,
ec_certificates, event, extra_images, extra_links, extra_texts, extra_videos,
images, is_reshared, link_description, link_url, page_categories, page_id,
page_is_deleted, page_like_count, page_name, page_profile_picture_url,
page_profile_uri, root_reshared_post, title, videos
```

### Traps
- **Media lives in lists**, not on the snapshot directly:
  `snapshot.videos[0].video_hd_url`, `snapshot.images[0]`. An earlier version
  read `snapshot.video_hd_url` and silently returned `null` for every ad.
- **Dates and delivery live on the OUTER object**, not in `snapshot`:
  `start_date`, `end_date`, `is_active`, `publisher_platform`.
- `body` is `{"text": "..."}`, not a string. `title` IS a plain string.
- `videos[0]` keys: `video_hd_url`, `video_sd_url`, `video_preview_image_url`,
  `watermarked_video_hd_url`, `watermarked_video_sd_url`.

### Fields that are legitimately null — do not chase them
Meta does not publish these for commercial (non-political, non-EU) ads:
`spend`, `reach_estimate`, `currency`, `impressions_with_index.impressions_text`,
`total_active_time`, `collation_count`.

They are kept in the response because they DO populate for EU and political ads.
If they come back null, that is correct behaviour, not a bug. **Do not add
fallbacks that invent values for them.**

### Bonus signal — already implemented, keep it
fbcdn video URLs carry a base64 `efg` query param that decodes to:
```json
{"duration_s": 31, "asset_age_days": 140, "xpv_asset_id": 1521563286355949}
```
`asset_age_days` vs the ad's own age is a strong "creative was validated
elsewhere first" signal. `decode_efg()` handles this.

---

## 3. Environment — exact setup

The dev machine is macOS with **Python 3.14**, which breaks some packages.

```bash
cd ~/Desktop/adspy
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Rules:**
- Always run tools as `python3 -m uvicorn ...`, never bare `uvicorn`. macOS
  resolves the bare command to a Homebrew Python outside the venv, which does
  not have `curl_cffi` installed, producing a confusing `ModuleNotFoundError`.
- Do **not** install `uvicorn[standard]`. Its optional C extensions fail to
  build on Python 3.14. Plain `uvicorn` is sufficient.

Run the server:
```bash
python3 -m uvicorn app.main:app --port 8000
```

Offline parser check against a saved page (no network):
```bash
python3 -m app.main ad_page.html
```

---

## 4. YOUR TASKS, in order

### Phase A — Prove the current build works
1. Set up the venv and install dependencies.
2. Start the server. Confirm `GET /api/health` returns `{"ok": true, ...}`.
3. Run `POST /api/extract` against this known-good ad:
   ```
   https://www.facebook.com/ads/library/?id=1277136211266420
   ```
   Expected: `page_name` = ThreadBeast, `video_hd_url` non-null,
   `start_date_ts` = 1778223600, `is_active` = true,
   `publisher_platforms` has 5 entries, `video_duration_s` = 31.

   Note: this ad may have stopped running by the time you read this. If it
   returns nothing, pick a different active video ad and say so in your report.

**STOP HERE if Phase A fails.** Report the exact error and do not proceed.

### Phase B — Test across ad types
Go to the Meta Ad Library, find real ads for each case below, and run each
through `/api/extract`. Prefer Indian advertisers where possible.

| # | Case | What to verify |
|---|---|---|
| 1 | Video ad | `video_hd_url`, `video_duration_s` populated |
| 2 | Single image ad | `image_url` populated, `display_format` = IMAGE |
| 3 | Carousel / multi-card | `cards` array populated with per-card media |
| 4 | DCO ad (multiple creatives) | `extra_videos` / `extra_images` handled |
| 5 | Inactive / ended ad | `is_active` = false, still parses |
| 6 | Indian advertiser | `page_categories`, `page_like_count` populated |
| 7 | Invalid URL | clean `400`, not a stack trace |
| 8 | Non-existent ad id | clean `502` with a readable message |

Record the full JSON for each. Where a field comes back null, decide whether
that is Meta not publishing it (fine) or our parser missing it (fix it).

### Phase C — Harden the parser
Fix only the real gaps found in Phase B. Constraints:
- Keep the shape-based search. Do not introduce hard-coded JSON paths.
- Keep every existing field in the response. Add new ones freely.
- Every new field must be justified by a real ad that populates it.

### Phase D — Verify the download proxy
1. Take a `video_hd_url` from a successful extract.
2. `GET /api/proxy?url=<encoded>&filename=test.mp4`
3. Confirm: HTTP 200, `Content-Disposition: attachment`, and the saved file is a
   real playable MP4 (check the byte size and that it opens).
4. Confirm the host allowlist rejects a non-Meta URL, e.g.
   `?url=https://example.com/x.mp4` must return `400`.
5. Test with an fbcdn URL that has expired (reuse one an hour old). Confirm the
   error is readable rather than a hang or a truncated file.

### Phase E — Tests
Write `pytest` tests in `tests/`:
- Parser tests using saved HTML fixtures. **No network in tests.** Save real
  pages to `tests/fixtures/*.html` during Phase B and assert against them.
- `extract_ad_id()` against: full URL, bare id, URL with extra params, garbage.
- `decode_efg()` against a real fbcdn URL and against a URL with no `efg`.
- Host allowlist: allowed hosts pass, everything else is rejected.

---

## 5. HARD RULES

**Do not:**
- Add Playwright, Selenium, or any headless browser. The plain-HTTP path is
  proven to work. Only revisit this if Phase A fails and you have shown in your
  report exactly why plain HTTP can no longer work.
- Remove or weaken the rate limiter in `rate_limit()`.
- Remove or weaken the host allowlist in `/api/proxy`. Without it the endpoint
  is an open proxy that anyone on the internet can abuse.
- Store, cache, or write any downloaded video to disk in the request path.
  Stream only.
- Add API keys, tokens, or credentials of any kind. This service is
  intentionally unauthenticated.
- Add a paid third-party scraping API.
- Widen CORS to `*`. Keep `ALLOWED_ORIGINS` explicit.

**Do:**
- Keep `/api/extract` under 8 seconds on a warm session.
- Make every error message something a non-technical user could act on.
- Keep the code in one module unless it exceeds ~600 lines.
- Log the ad id on every parse failure so failures are diagnosable later.

---

## 6. WHAT TO REPORT BACK

Write your findings to `docs/REPORT.md` using exactly this structure:

```markdown
# AdSpy Backend — Agent Report

## 1. Environment
Python version, install issues, anything that needed working around.

## 2. Phase A result
Pass or fail. If pass, paste the full JSON for the reference ad.

## 3. Phase B matrix
One row per test case: ad id, advertiser, what worked, what came back null,
and whether that null is Meta's doing or ours.

## 4. Parser changes made
What you changed and which specific ad forced each change.

## 5. Download proxy
Byte size, whether the MP4 played, allowlist rejection confirmed,
expired-URL behaviour.

## 6. Open problems
Anything still broken or uncertain. Be blunt — an unreported problem is
worse than a reported one.

## 7. Ad types NOT yet handled
Formats you could not find examples of, or that parse incompletely.
```

Keep it factual. Paste real JSON rather than describing it. If something did not
work, say so plainly instead of reporting partial success as success.
