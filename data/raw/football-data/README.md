# Local historical CSV inputs

Place season/division CSV files in this directory before manually running the
**Build experimental historical dataset** GitHub Actions workflow.

Expected filename pattern: `<league_code>_<season>.csv`, for example
`E0_2526.csv` or `D1_2425.csv`. The league code is the exact football-data.co.uk
division code used by the builder. Unknown codes are reported and skipped.

Required columns:
- `Date` (DD/MM/YY, DD/MM/YYYY, or YYYY-MM-DD)
- `HomeTeam`, `AwayTeam`
- `FTHG`, `FTAG` (full-time home/away goals)

Optional columns used for corners/cards and other features:
- `HC`, `AC` (home/away corners)
- `HY`, `AY` (home/away yellow cards)
- `HF`, `AF` (home/away fouls)
- `HS`, `AS` (home/away shots)
- `HST`, `AST` (home/away shots on target)
- `HR`, `AR` (home/away red cards; not prediction targets)
- `Referee`

The workflow does not download provider data. Overlapping match rows from multiple
files are deduplicated by league, date, home team, and away team. Features are
computed only from prior match dates; same-day fixtures are held out of one
another's histories.

After the workflow succeeds, inspect `historical_dataset_report.json` in the
artifact. Confirm coverage and completeness by competition before proceeding to
model training. Do not assume the builder's static eligible-league list proves
sufficient historical coverage.
