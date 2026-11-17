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
