# AGENT TASK 3 — Scoring recalibration (short round)

Round 2 accepted. The proxy fix and the integration are both correct, and the
three measurements were exactly what was needed.

Your Q2 result exposed a real bug in the scoring engine. You reported it as a
pass — *"Image and carousel ads are NOT buried"* — which was true but looked in
the wrong direction. The actual finding in your own numbers was:

```
IMAGE 67.0  >  DPA 65.5  >  VIDEO 60.5  >  DCO 53.8
```

**Video ads were scoring lowest.** Cross-referenced with your Q3 answer
(`creative_asset_age_days` present in only 27% of ads), the cause was that a
video whose duration Meta withheld fell through every format branch and earned
0 format points, while an image ad earned 3 unconditionally. Ads were being
penalised for metadata Meta chose not to publish.

That is the kind of thing worth flagging next time even when it is not what the
question literally asked about. Good measurement, incomplete read.

---

## The only job this round

`app/scoring.py` has been replaced with a corrected version. Two changes:

1. **Unknown `creative_asset_age_days` no longer costs 8 proof points.** Absence
   is missing evidence, not evidence of weakness, so unknown now scores the same
   as known-but-fresh.
2. **Format fitness has a floor.** A video with no published duration scores the
   same 3 points as an image. `DCO`, `DPA`, `CAROUSEL` and
   `AUTOMATED_ANIMATION` get 5, matching the multi-card branch.

### Do this

1. Replace `app/scoring.py` with the new file. Do not merge by hand — use it
   whole.
2. Re-run the full existing test suite. All 25 tests must still pass. If any
   fails, that is a signal the fix broke something. Report it, do not patch
   around it.
3. Re-run your 37-ad fixture set through scoring and report, **as a table**:
   - min / max / mean / median score
   - mean score grouped by `display_format`
   - how many ads land in each verdict band
4. Confirm the format spread has closed. Expected: under 8 points between the
   highest and lowest format average. If it is still above 8, report which
   format is the outlier and your reading of why — **do not change any weight.**

### Same hard rules

Weights, thresholds and bands stay untouched. No Playwright. Rate limiter and
proxy host allowlist stay. No auth, no paid APIs, no `*` CORS.

---

## Report

Append `## Round 3` to `docs/REPORT.md` with the table, the spread number, and
the verdict-band distribution. Short is fine — this is a measurement round, not
a build round.

**After this, the backend is frozen.** Do not start new features, refactors,
caching layers, or a database. If you see something worth doing, list it under a
`## Backlog` heading and leave it alone. Frontend work begins next and the API
shape needs to stop moving.
