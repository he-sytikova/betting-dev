# Betting Dev

Development preview repository for the football predictions app.

Source: `he-sytikova/betting` branch `model-development`.

GitHub Pages: enable **Settings → Pages → Deploy from a branch → main / root** after the development files are copied here.

## Home-page daily board

`daily-board.js` calculates current Moscow-day candidates from the active model artifacts and `data.json`. It shows only future, model-resolved fixtures with `p_model >= 0.70`; no odds, fair probability or EV are invented before bookmaker lines are processed. A stale `data.json` date hides both daily sections.

Prepared singles and doubles are read from `analysis/doubles_YYYY-MM-DD.json`:

```json
{
  "date": "YYYY-MM-DD",
  "data_status": "not_processed | source_unavailable | no_line_match | ok",
  "prepared_coupons": []
}
```

Each coupon has either one or two `legs`. Legs contain fixture identity, market `label`, `p_model`, decimal `odds`, and `bookmaker`; optional `fair`, `delta_pp`, and `ev` are display metrics. Coupon-level values may be supplied as `odds`, `probability`, and `ev`, or derived from the legs. A double with legs from different bookmakers is rejected. Every rendered coupon is labelled **prepared, not placed**, regardless of input status. This module never places bets or records financial results.

## Daily lines-to-coupons pipeline

Run acquisition first, then pass its gated fixtures, matching audit and enriched snapshots to the coupon generator:

```bash
python3 scripts/acquire_bookmaker_lines.py \
  --date YYYY-MM-DD \
  --fixtures data.json \
  --output probe-out/lines_YYYY-MM-DD

node scripts/lines/build_daily_coupons.cjs \
  --data data.json \
  --acquisition probe-out/lines_YYYY-MM-DD \
  --output analysis/doubles_YYYY-MM-DD.json
```

The generator reuses `DailyBoard.buildCandidates` and the active model artifacts. It does not recalculate probabilities in another language and does not perform fuzzy event matching. A candidate reaches pricing only through a `matched` record in `matching.json` for the same gated fixture and kickoff.

Calculations and admission rules:

- 1X2 fair probabilities are normalized inverse prices; DC fair is the corresponding sum from the same bookmaker's full 1X2.
- A two-sided total requires both sides of the exact line. Integer two-way lines remain excluded.
- `EV = p_model × odds`; `delta_pp = (p_model − fair) × 100`; minimum price is `1.03 / p_model`, rounded upward to 0.01.
- Admission requires `p_model >= 0.70`, `EV >= 1.03`, `abs(delta_pp) < 15`, and the minimum-price check. Over selections additionally require `p_model >= fair`.
- Only confirmed model/bookmaker markets are priced. Yellow cards, fouls, shots, shots on target and European cups are not admitted by this pipeline.
- Duplicate market prices are reduced to the highest eligible bookmaker price; losing prices remain in diagnostics.
- The homepage artifact contains at most two singles and one double, ranked by model probability, then EV and price. A double uses different matches and one bookmaker. Combined odds, probability and EV are products of the leg values; its minimum price is the product of leg minimums.
- For a later session, `--used-teams session-teams.json` accepts an array (or `{ "teams": [] }`) of teams already used in doubles. Such teams are excluded from new doubles.

The full rejection journal is written to `probe-out/lines_YYYY-MM-DD/coupon-diagnostics.json` by default. The public JSON contains aggregate diagnostics only. Raw responses, snapshots and the detailed journal are not committed. A processed day with no qualifying coupons is published as `data_status: "ok"` with an empty `prepared_coupons` array; no demonstration probabilities or coupons are synthesized.
