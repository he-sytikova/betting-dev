# Betting Dev

Development preview repository for the football predictions app.

Source: `he-sytikova/betting` branch `model-development`.

GitHub Pages: enable **Settings → Pages → Deploy from a branch → main / root** after the development files are copied here.

## Home-page daily board

`daily-board.js` calculates current Moscow-day candidates from the active model artifacts and `data.json`. It shows only future, model-resolved fixtures with `p_model >= 0.75`; no odds, fair probability or EV are invented before bookmaker lines are processed. A stale `data.json` date hides both daily sections.

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
- Admission requires `p_model >= 0.75`, `EV >= 1.03`, `abs(delta_pp) < 15`, and the minimum-price check. Over selections additionally require `p_model >= fair`.
- Only confirmed model/bookmaker markets are priced. Yellow cards, fouls, shots, shots on target and European cups are not admitted by this pipeline.
- Duplicate market prices are reduced to the highest eligible bookmaker price; losing prices remain in diagnostics.
- The Moscow game day is divided into four exact sessions: morning 06:00–12:00, day 12:00–18:00, evening 18:00–00:00 and night 00:00–06:00. Every kickoff belongs to exactly one session.
- Each session independently contains at most two singles and one double, ranked by model probability, then EV and price: no more than twelve prepared money coupons per day. A double uses different matches from the same session and one bookmaker. Combined odds, probability and EV are products of the leg values; its minimum price is the product of leg minimums.
- For a later session, `--used-teams session-teams.json` accepts an array (or `{ "teams": [] }`) of teams already used in doubles. Such teams are excluded from new doubles.

Possible winnings can be used only after actual bookmaker settlement, on later events in the following session. Settlement time and funds availability remain bookmaker-controlled; the site does not transfer money or perform financial accounting automatically.

The separate daily-express page admits goal-model legs with `p_model >= 0.75` and publishes at most seven two-leg expresses. Its existing price and EV rules are independent of the per-session prepared-money-coupon limits.

The full rejection journal is written to `probe-out/lines_YYYY-MM-DD/coupon-diagnostics.json` by default. The public JSON contains aggregate diagnostics only. Raw responses, snapshots and the detailed journal are not committed. A processed day with no qualifying coupons is published as `data_status: "ok"` with an empty `prepared_coupons` array; no demonstration probabilities or coupons are synthesized.

## Automated daily publication

The **Формирование купонов** workflow runs at 09:35 Moscow time, after the 09:17 fixture refresh, and also supports manual `workflow_dispatch` with an optional date and detail limit. A requested date must exactly match `data.json`; a stale or malformed model snapshot stops the run before any bookmaker request and does not change the homepage artifact.

The workflow has non-cancelling concurrency, so overlapping runs cannot replace each other's result. It validates acquisition, pricing, homepage normalization, source capture times, coupon limits and the mandatory `prepared` status before publication. Bookmaker responses are read-only inputs: no bet placement, stake or financial result is recorded.

Only `analysis/doubles_YYYY-MM-DD.json` can be committed by this automation. Raw responses, snapshots, enriched lines and detailed diagnostics remain in the ignored `probe-out/` directory and are uploaded only as retained Actions artifacts. Unexpected acquisition or validation failures produce no repository commit. If both bookmakers complete with an unavailable status, the validated `source_unavailable` artifact is published instead.

GitHub Pages deploys `analysis/*.json` changes and also runs after successful completion of **Формирование купонов**, because commits made with the Actions token do not trigger an ordinary push event.
