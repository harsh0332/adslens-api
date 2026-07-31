# AGENT TASK 2 — Proxy fix + scoring integration

The Phase A-E report is accepted. Good work on the ad_id matching fix.

Two jobs this round. Read both fully before starting.

---

## JOB 1 — Fix the expired-URL bug in `/api/proxy`

Your report listed this as passing:

> Expired URL Handling: Expired Meta CDN URL returned HTTP 200 OK with 0 bytes
> clean empty stream.

**That is a bug, not a pass.** A user clicks Download, gets a 0-byte `.mp4`, and
concludes the site is broken. There is no error anywhere for the frontend to
show. An empty file is a worse failure than a visible error, because it is
silent.

### Why it happens
`StreamingResponse` commits the status line and headers before the generator
body runs. By the time the generator discovers the upstream returned 403/404/410,
`200 OK` has already gone out. The `if resp.status_code != 200: return` inside
the generator ends the stream but cannot change the status.

### Required fix
Open the upstream stream and check its status **before** constructing the
`StreamingResponse`, then hand the already-open response to the generator.

Behaviour to implement:
- Upstream non-200 → raise `HTTPException` with a message the frontend can show
  verbatim, e.g. *"This download link has expired. Re-run the extract to get a
  fresh one."* Use status `410` for an expired link (403/404/410 upstream) and
  `502` for anything else.
- Upstream 200 but `Content-Length: 0` → same treatment. Never stream an empty
  body to the browser as a successful download.
- Forward the upstream `Content-Length` when present so the browser shows a real
  progress bar.
- The session must still be closed on every path, including the error paths.
  Verify no socket leak by running 20 sequential proxy requests and confirming
  the process file-descriptor count is stable.

### Verify
1. Fresh URL → 200, correct byte count, playable MP4.
2. Expired URL → 410 with the readable message. **Confirm no file is saved.**
3. Non-Meta host → 400 (unchanged).
4. Mid-stream disconnect (client aborts) → server logs cleanly, no crash.

---

## JOB 2 — Wire in the scoring engine

`app/scoring.py` is new, complete, and already unit-calibrated. It is a pure
function: no network, no I/O, no clock surprises (`now` is injectable).

**Do not rewrite the scoring logic or retune the weights.** They are calibrated
deliberately against a competitor's model. If you believe a weight is wrong,
write it in the report — do not change it.

### 2.1 Integrate
- `POST /api/extract` returns the existing ad fields plus a new top-level
  `"score"` object from `score_ad(ad)`.
- Scoring must never break extraction. Wrap the call so that if `score_ad`
  raises, the endpoint still returns the ad data with `"score": null` and logs
  the exception with the ad id.
- Add `POST /api/score` taking a normalised ad dict and returning just the score
  object. This lets the frontend re-score cached ads without hitting Meta.

### 2.2 Test with real ads
Re-run the ads from your Phase B matrix through the scored endpoint. For each,
record: `score`, `verdict`, `proof_score`, `craft_score`, `stage`, `confidence`,
and the counts of `why_it_works` / `watch_outs` / `steal`.

Then answer these three questions with evidence:

1. **Does any ad score above 90 or below 10?** Those extremes should be rare. If
   they are common, the curve is mis-centred — report the distribution, do not
   adjust the weights yourself.
2. **Do image-only and carousel ads score fairly against video ads?** Video ads
   can earn 6 format points via duration; images earn 3, carousels 5. Check
   whether that systematically buries image ads. Report the average score by
   `display_format`.
3. **Is `creative_asset_age_days` present often enough to matter?** It is worth
   8 proof points. Report how many of your test ads actually had it. If it is
   mostly null, say so plainly — that changes how much the frontend should lean
   on it.

### 2.3 Tests
Add `tests/test_scoring.py`, offline only:
- A high-quality old ad scores >= 75.
- A thin brand-new ad scores <= 30.
- A strong 24-day-old ad scores >= 65. **This is the specific case a rival tool
  caps at 55; it is the reason this engine exists. Guard it with a test.**
- Missing `start_date_ts` → `days_running` is null, `confidence` is not "High",
  and nothing raises.
- An ad dict containing only `{"body": "x"}` does not raise.
- `score` is always an int in 0-100 and `proof_score + craft_score == score`.

---

## HARD RULES — unchanged, still binding

Do not add Playwright. Do not weaken the rate limiter. Do not remove the
`/api/proxy` host allowlist. Do not persist media to disk. Do not add auth or a
paid scraping API. Do not widen CORS to `*`.

New for this round:
- Do not change any scoring weight, threshold, or band in `app/scoring.py`.
- Do not make `/api/extract` slower than 8 seconds on a warm session. Scoring is
  pure computation and should add under 5 ms — measure it and report the number.

---

## REPORT

Append to `docs/REPORT.md` under a new heading `## Round 2`:

1. **Proxy fix** — what changed, and the four verification results.
2. **Scoring integration** — how failures are isolated, measured latency.
3. **Score table** — every Phase B ad with its full score breakdown.
4. **The three questions** — answered with numbers, not impressions.
5. **Weights you think are wrong** — and why. Recommend, do not change.
6. **Anything still broken.**

Be blunt about what does not work. The last report marked a 0-byte download as a
pass; that cost a round. An unreported problem is worse than a reported one.
