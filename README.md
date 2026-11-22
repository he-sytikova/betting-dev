# Betting Dev

Development preview repository for the football predictions app.

Source: `he-sytikova/betting` branch `model-development`.

GitHub Pages: enable **Settings → Pages → Deploy from a branch → main / root** after the development files are copied here.

## Home-page daily board

`daily-board.js` calculates current Moscow-day candidates from the active model artifacts and `data.json`. It shows only future, model-resolved fixtures with `p_model >= 0.75`; no odds, fair probability or EV are invented before bookmaker lines are processed. A stale `data.json` date hides both daily sections.

The candidate panel has display-only filters for every market family and bookmaker present: goals, corners, yellow cards, fouls, shots, shots on target, Nations League, Fonbet, Marathonbet and Winline when available. Market and bookmaker filters combine, so the panel can show only corners priced by Marathonbet, for example. Both selections survive data refreshes and update all visible counts. When a bookmaker is selected, its exact quote replaces the best-price column. These filters never change candidate admission or coupon selection.

Prepared singles and doubles are read from `analysis/doubles_YYYY-MM-DD.json`:

```json
{
  "date": "YYYY-MM-DD",
  "data_status": "not_processed | source_unavailable | no_line_match | ok",
  "candidate_prices": [],
  "prepared_coupons": []
}
```

`candidate_prices` contains one reference price per candidate and bookmaker, up to three rows for Fonbet, Marathonbet and Winline. Each row includes `bookmaker`, `match_id`, `market_key` and `odds`; if duplicate quotes exist for the same bookmaker, the highest valid price is retained. The UI joins prices by the canonical model key, kickoff, model and market label, uses them for bookmaker filtering and still calculates the best available price independently. These quotes are informational, may fail coupon admission, and do not replace fair/EV checks. Missing, unsupported or unconfirmed markets have no price and are shown as `—`.

Acquisition gates future fixtures covered by any homepage candidate model: goals, corners/cards, stats or Nations League. All four model families participate in event matching and price indexing, but this does not expand automatic coupon admission: unsupported stats markets, yellow cards, fouls, shots and shots on target remain unpriced for money coupons.

Fonbet, Marathonbet and Winline are discovered through their general public football catalogs rather than fixed internal tournament allowlists. Marathonbet enumerates every championship exposed by its all-tournaments catalog; Winline enumerates every public football country and opens club events through their country pages. Event matching still requires exact canonical team identities, kickoff within the existing 20-minute tolerance and an exact supported market. Winline uses only explicit team aliases and an exact bilingual identity link to a unique Marathonbet event with the same teams and kickoff; unknown or ambiguous names remain unmatched. A successful catalog response therefore does not imply that every displayed team name has a canonical model identity. Missing events and markets remain audited absences and are never replaced with synthetic quotes.

By default, enrichment requests details for every unique matched `(bookmaker, match_id)`; duplicate fixture variants reuse the same captured markets. A non-negative `detail_limit` is an optional manual diagnostic cap on unique matched-event requests, and `0` disables detail requests. The acquisition summary records the limit mode, unique and deduplicated detail counts, and per-tournament matching/detail coverage.

Each coupon has either one or two `legs`. Legs contain fixture identity, market `label`, `p_model`, decimal `odds`, and `bookmaker`; optional `fair`, `delta_pp`, and `ev` are display metrics. Coupon-level values may be supplied as `odds`, `probability`, and `ev`, or derived from the legs. A double with legs from different bookmakers is rejected. Every rendered coupon is labelled **prepared, not placed**, regardless of input status. This module never places bets or records financial results.

## Results and owner-confirmed financial reports

`scripts/models/fetch-euro365-results.py` normalizes final Euro365 `/v1/scores` results for the current Moscow game day and publishes public sports data only:

- `data/results/YYYY-MM-DD.json` contains fixture identity, status and the score fields needed for supported market settlement;
- `data/results/index.json` lists the available days, so the browser does not request missing artifacts;
- a structured scores response is primary, while the already published `actualResult` snapshot is a bounded fallback only after a finished/full-time state;
- live or incomplete matches stay unsettled; postponed matches remain pending, while cancelled and abandoned matches produce a void result.

The homepage links to the dedicated `financial-report.html` page **Результаты и отчётность**, which uses only coupons explicitly marked **Проставлено** by the owner. Confirmed amount, money/freebet type, bookmaker, odds and coupon composition remain in browser `localStorage`; neither these financial records nor manual reconciliation payouts are written to `data.json`, results artifacts or the repository. GitHub Actions therefore cannot read or overwrite them.

Automatic settlement supports the proven 1X2, double-chance, goal-total and individual-total markets. Corners are settled only when both the corners model and total-corners result are present. Results are matched by Euro365 event ID, with an exact canonical home/away/kickoff fallback for older records. Unsupported or ambiguous matches remain pending rather than being guessed. A double with a winning and a void leg uses the remaining frozen leg odds; any losing leg makes the double a loss.

Reports cover a selected Moscow game day, its Monday–Sunday week or its calendar month, and attribute each bet to its original `record.day`. Money stakes, payouts, net result and ROI are separate from freebet nominal and net winnings. The owner can additionally enter the actual bookmaker payout as a manual reconciliation; calculated and confirmed totals remain separate. This local history is not synchronized between browsers or devices. Dated results accumulate after deployment, so older bets without a results artifact stay pending automatically but can still be reconciled manually. The Odds API reserve and historical results backfill are not enabled by this implementation.

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
- The Moscow game day uses five coupon sessions: session 1 runs 12:00–15:00, session 2 runs 15:00–18:00, session 3 runs 18:00–21:00, session 4 runs 21:00–23:55 and session 5 runs 23:55–07:00 across midnight. Session start boundaries are inclusive and end boundaries exclusive, so 23:55 belongs to session 5 and 07:00 is outside all sessions. Kickoffs outside these windows do not enter prepared coupons.
- Each session independently contains at most five singles and two doubles, ranked by model probability, then EV and price: no more than 35 prepared money coupons per day. A double uses different matches from the same session and one bookmaker. Combined odds, probability and EV are products of the leg values; its minimum price is the product of leg minimums.
- For a later session, `--used-teams session-teams.json` accepts an array (or `{ "teams": [] }`) of teams already used in doubles. Such teams are excluded from new doubles.

Possible winnings can be used only after actual bookmaker settlement, on later events in the following session. Settlement time and funds availability remain bookmaker-controlled; the site does not transfer money or place bets. Financial reporting covers only owner-confirmed placements and remains local to the browser as described above.

The separate daily-express page admits goal-model legs with `p_model >= 0.75` and publishes at most seven two-leg expresses. Its existing price and EV rules are independent of the per-session prepared-money-coupon limits.

The full rejection journal is written to `probe-out/lines_YYYY-MM-DD/coupon-diagnostics.json` by default. The public JSON contains aggregate diagnostics only. Raw responses, snapshots and the detailed journal are not committed. A processed day with no qualifying coupons is published as `data_status: "ok"` with an empty `prepared_coupons` array; no demonstration probabilities or coupons are synthesized.

## Automated daily publication

The **Формирование купонов** workflow runs at 09:35 Moscow time, after the 09:17 fixture refresh, and also supports manual `workflow_dispatch` with an optional date and detail limit. A blank detail limit means unlimited unique matched-event enrichment; a non-negative integer is a manual cap, and `0` means no detail requests. Scheduled runs leave the limit blank. A requested date must exactly match `data.json`; a stale or malformed model snapshot stops the run before any bookmaker request and does not change the homepage artifact.

The workflow has non-cancelling concurrency, so overlapping runs cannot replace each other's result. It validates acquisition, pricing, homepage normalization, source capture times, coupon limits and the mandatory `prepared` status before publication. Bookmaker responses are read-only inputs: no bet placement, stake or financial result is recorded.

Only `analysis/doubles_YYYY-MM-DD.json` can be committed by this automation. Raw responses, snapshots, enriched lines and detailed diagnostics remain in the ignored `probe-out/` directory and are uploaded only as retained Actions artifacts. Unexpected acquisition or validation failures produce no repository commit. If all bookmakers complete with an unavailable status, the validated `source_unavailable` artifact is published instead.

GitHub Pages deploys `analysis/*.json` changes and also runs after successful completion of **Формирование купонов**, because commits made with the Actions token do not trigger an ordinary push event.
