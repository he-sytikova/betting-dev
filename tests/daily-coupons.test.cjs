const test = require('node:test');
const assert = require('node:assert/strict');

const coupons = require('../scripts/lines/build_daily_coupons.cjs');
const board = require('../daily-board.js');

const DAY = '2099-01-01';
const NOW = `${DAY}T12:00:00.000Z`;

function candidate(overrides = {}) {
  return {
    time: Date.parse(`${DAY}T18:00:00Z`), modelKey: 'model|E0|newcastle|everton',
    league: 'England. Premier League', home: 'Newcastle', away: 'Everton',
    modelLabel: 'Голы', label: '1X', probability: 0.8,
    oMinExact: 1.2875, oMinStep: 1.29, lineStatus: 'not_checked', ...overrides,
  };
}

function line(overrides = {}) {
  return {
    bookmaker: 'Fonbet', match_id: '100',
    odds: { home: 2, draw: 4, away: 4 },
    markets: {
      DC: { '1X': 1.4, 12: 1.5, X2: 2 },
      'TOTAL_GOALS:2.5': { ТБ: 1.5, ТМ: 3 },
    },
    ...overrides,
  };
}

function eligibleLeg(overrides = {}) {
  return {
    time: `${DAY}T18:00:00.000Z`, model_key: 'model|E0|newcastle|everton',
    league: 'England. Premier League', home: 'Newcastle', away: 'Everton',
    model: 'Голы', label: '1X', p_model: 0.8, bookmaker: 'Fonbet', match_id: '100',
    market_key: 'DC', odds: 1.4, fair: 0.75, delta_pp: 5, ev: 1.12,
    o_min_exact: 1.2875, o_min_step: 1.29, ...overrides,
  };
}

test('fair 1X2 removes margin and DC fair derives from the same 1X2', () => {
  const fair = coupons.fairFrom1x2({ home: 2, draw: 3, away: 4 });
  assert.ok(Math.abs(fair.home - 0.46153846153846156) < 1e-12);
  assert.ok(Math.abs(fair.draw - 0.3076923076923077) < 1e-12);
  assert.ok(Math.abs(fair.away - 0.23076923076923078) < 1e-12);
  assert.ok(Math.abs(fair['1X'] - (fair.home + fair.draw)) < 1e-12);
  assert.ok(Math.abs(fair['12'] - (fair.home + fair.away)) < 1e-12);
  assert.ok(Math.abs(fair.X2 - (fair.draw + fair.away)) < 1e-12);
  assert.equal(coupons.fairFrom1x2({ home: 2, draw: 3 }), null);
});

test('two-sided fair, EV, delta and minimum price are attached to an eligible total', () => {
  const result = coupons.evaluatePrice(candidate({ label: 'ТБ 2.5', probability: 0.75 }), line());
  assert.ok(result.leg, result.rejection?.reason);
  assert.ok(Math.abs(result.leg.fair - 2 / 3) < 1e-12);
  assert.ok(Math.abs(result.leg.ev - 1.125) < 1e-12);
  assert.ok(Math.abs(result.leg.delta_pp - (0.75 - 2 / 3) * 100) < 1e-12);
  assert.equal(result.leg.o_min_step, 1.38);
  assert.equal(result.leg.market_key, 'TOTAL_GOALS:2.5');
});

test('DC needs its selected price and full 1X2, but not the other DC prices', () => {
  const input = line({ markets: { DC: { '1X': 1.4 } } });
  const result = coupons.evaluatePrice(candidate(), input);
  assert.ok(result.leg, result.rejection?.reason);
  assert.equal(result.leg.fair, 0.75);
  assert.ok(Math.abs(result.leg.delta_pp - 5) < 1e-12);
  const no1x2 = coupons.evaluatePrice(candidate(), line({ odds: { home: 2 } }));
  assert.equal(no1x2.rejection.reason, 'incomplete_1x2');
});

test('filter order rejects low EV before interpreting a large delta', () => {
  const result = coupons.evaluatePrice(candidate({ label: 'ТБ 2.5', probability: 0.7 }), line({
    markets: { 'TOTAL_GOALS:2.5': { ТБ: 1.47, ТМ: 5 } },
  }));
  assert.equal(result.rejection.reason, 'ev_below_threshold');
  assert.ok(result.rejection.ev < 1.03);
  assert.ok(Number.isFinite(result.rejection.fair));
});

test('absolute delta of 15 percentage points is an anomaly in either direction', () => {
  const positive = coupons.evaluatePrice(candidate({ label: 'ТБ 2.5' }), line({
    markets: { 'TOTAL_GOALS:2.5': { ТБ: 1.5, ТМ: 2.5 } },
  }));
  assert.equal(positive.rejection.reason, 'delta_anomaly');
  const negative = coupons.evaluatePrice(candidate({ label: 'ТМ 2.5' }), line({
    markets: { 'TOTAL_GOALS:2.5': { ТБ: 30, ТМ: 1.5 } },
  }));
  assert.equal(negative.rejection.reason, 'delta_anomaly');
});

test('over below fair is rejected while the opposite side has no over-only rule', () => {
  const markets = { 'TOTAL_GOALS:2.5': { ТБ: 1.5, ТМ: 5 } };
  const over = coupons.evaluatePrice(candidate({ label: 'ТБ 2.5', probability: 0.72 }), line({ markets }));
  assert.equal(over.rejection.reason, 'over_below_market');
  const under = coupons.evaluatePrice(candidate({ label: 'ТМ 2.5', probability: 0.72 }), line({
    markets: { 'TOTAL_GOALS:2.5': { ТБ: 5, ТМ: 1.5 } },
  }));
  assert.ok(under.leg, under.rejection?.reason);
});

test('incomplete pairs, integer lines and unconfirmed model markets never enter coupons', () => {
  const incomplete = coupons.evaluatePrice(candidate({ label: 'ТБ 2.5' }), line({
    markets: { 'TOTAL_GOALS:2.5': { ТБ: 1.5 } },
  }));
  assert.equal(incomplete.rejection.reason, 'incomplete_market');
  const integer = coupons.evaluatePrice(candidate({ label: 'ТБ 2' }), line({
    markets: { 'TOTAL_GOALS:2': { ТБ: 1.5, ТМ: 3 } },
  }));
  assert.equal(integer.rejection.reason, 'integer_two_way_line');
  const cards = coupons.evaluatePrice(candidate({ modelLabel: 'ЖК', label: 'ТБ 2.5' }), line());
  assert.equal(cards.rejection.reason, 'market_not_confirmed');
  const euro = coupons.evaluatePrice(candidate({ modelLabel: 'Еврокубки' }), line());
  assert.equal(euro.rejection.reason, 'market_not_confirmed');
});

test('deduplication keeps the best price across three bookmakers and audits losers', () => {
  const best = eligibleLeg({ bookmaker: 'Winline', odds: 1.55, ev: 1.24 });
  const marathon = eligibleLeg({ bookmaker: 'Marathonbet', odds: 1.5, ev: 1.2 });
  const lower = eligibleLeg({ odds: 1.4, ev: 1.12 });
  const otherLine = eligibleLeg({ label: 'ТБ 2.5', market_key: 'TOTAL_GOALS:2.5', odds: 1.45 });
  const result = coupons.dedupeEligible([lower, otherLine, marathon, best]);
  assert.equal(result.selected.length, 2);
  assert.equal(result.selected.find(row => row.label === '1X').bookmaker, 'Winline');
  assert.equal(result.rejected.length, 2);
  assert.ok(result.rejected.every(row => row.reason === 'deduplicated_lower_price'));
  assert.ok(result.rejected.every(row => row.selected_bookmaker === 'Winline'));
  assert.ok(result.rejected.every(row => row.selected_odds === 1.55));
});

test('doubles use one bookmaker, different matches and exact combined formulas', () => {
  const a = eligibleLeg();
  const b = eligibleLeg({
    model_key: 'model|E0|arsenal|chelsea', home: 'Arsenal', away: 'Chelsea',
    match_id: '101', p_model: 0.75, odds: 1.5, ev: 1.125, o_min_step: 1.38,
  });
  const crossBookmaker = coupons.selectDoubles([a, { ...b, bookmaker: 'Marathonbet' }]);
  assert.equal(crossBookmaker.candidates, 0);
  const crossWinline = coupons.selectDoubles([
    { ...a, bookmaker: 'Winline' }, { ...b, bookmaker: 'Marathonbet' },
  ]);
  assert.equal(crossWinline.candidates, 0);
  const sameWinline = coupons.selectDoubles([
    { ...a, bookmaker: 'Winline' }, { ...b, bookmaker: 'Winline' },
  ]);
  assert.equal(sameWinline.coupons.length, 1);
  assert.ok(sameWinline.coupons[0].legs.every(leg => leg.bookmaker === 'Winline'));
  const sameMatch = coupons.selectDoubles([a, { ...b, model_key: a.model_key }]);
  assert.equal(sameMatch.candidates, 0);
  const result = coupons.selectDoubles([a, b]);
  assert.equal(result.coupons.length, 1);
  const coupon = result.coupons[0];
  assert.ok(Math.abs(coupon.odds - 2.1) < 1e-12);
  assert.ok(Math.abs(coupon.probability - 0.6) < 1e-12);
  assert.ok(Math.abs(coupon.ev - 1.26) < 1e-12);
  assert.ok(Math.abs(coupon.o_min_AB - 1.29 * 1.38) < 1e-12);
  assert.equal(coupon.status, 'prepared');
});

test('double selection excludes teams used in earlier sessions and repeated teams', () => {
  const a = eligibleLeg();
  const b = eligibleLeg({
    model_key: 'model|E0|arsenal|chelsea', home: 'Arsenal', away: 'Chelsea', match_id: '101',
  });
  assert.equal(coupons.selectDoubles([a, b], { usedTeams: ['Newcastle'] }).candidates, 0);
  const repeated = { ...b, home: 'Newcastle' };
  assert.equal(coupons.selectDoubles([a, repeated]).candidates, 0);
  assert.equal(coupons.selectDoubles([a, b], { usedTeams: ['  newcastle  '] }).candidates, 0);
});

test('Winline enriched lines participate in the common builder index', () => {
  const index = coupons.indexLines({
    Winline: { status: 'ok', matches: [line({ bookmaker: 'Winline', match_id: '17132350' })] },
  });
  assert.equal(index.get('Winline|17132350').bookmaker, 'Winline');
});

test('processed empty day is ok; missing sources and matches have distinct statuses', () => {
  const base = {
    date: DAY, data: {}, models: {}, gated: { generatedAt: NOW, fixtures: [] },
    generatedAt: NOW,
  };
  const ok = coupons.buildArtifact({
    ...base, matching: [{ status: 'matched', bookmaker: 'Fonbet' }],
    snapshots: {
      Fonbet: { status: 'ok', matches: [] },
      Marathonbet: { status: 'source_unavailable' },
      Winline: { status: 'source_unavailable' },
    },
  });
  assert.equal(ok.artifact.data_status, 'ok');
  assert.equal(ok.artifact.sources.Winline.status, 'source_unavailable');
  assert.deepEqual(ok.artifact.prepared_coupons, []);
  assert.deepEqual(ok.diagnostics.rejected, []);
  const noMatch = coupons.buildArtifact({
    ...base, matching: [], snapshots: { Fonbet: { status: 'ok', matches: [] } },
  });
  assert.equal(noMatch.artifact.data_status, 'no_line_match');
  const unavailable = coupons.buildArtifact({
    ...base, matching: [], snapshots: { Fonbet: { status: 'captcha', matches: [] } },
  });
  assert.equal(unavailable.artifact.data_status, 'source_unavailable');
});

function goalsModel() {
  return {
    schemaVersion: 2,
    leagueAliases: { 'england premier league': 'E0' },
    leagueNames: { E0: 'England. Premier League' }, leagueStats: { E0: {} },
    teams: { E0: {
      newcastle: { name: 'Newcastle', stats: {}, matchesBefore: 100 },
      everton: { name: 'Everton', stats: {}, matchesBefore: 100 },
    } },
    features: [], imputerMedian: [], scalerMean: [], scalerScale: [],
    models: {
      homeGoals: { type: 'poisson', features: [], coefficients: [], intercept: Math.log(3) },
      awayGoals: { type: 'poisson', features: [], coefficients: [], intercept: Math.log(0.2) },
    },
    marketLines: { totalGoals: [1.5, 2.5], teamGoals: [0.5, 1.5] },
    runtimePolicy: { minimumTeamMatches: 5, maxGoals: 12 },
  };
}

test('end-to-end artifact joins only exact gated audit identities and passes homepage normalization', () => {
  const fixture = {
    time: `${DAY}T18:00:00.000Z`, league: 'England. Premier League',
    home: 'Newcastle United', away: 'Everton FC', modelCoverage: 'goals',
  };
  const data = { date: DAY, predictions: [fixture] };
  const models = {
    goals: goalsModel(), cornersCards: null, stats: null,
    nations: { competitions: [], teamAliases: {}, teams: {} },
  };
  const expectedCandidates = board.buildCandidates(data, models, Date.parse(NOW));
  assert.ok(expectedCandidates.length > 0);
  assert.ok(expectedCandidates.every(row => row.modelKey === 'model|E0|newcastle|everton'));
  const matching = [{
    bookmaker: 'Fonbet', fixture_key: 'newcastle united|everton fc|2026-01-01',
    home: fixture.home, away: fixture.away, time: fixture.time, status: 'matched', match_id: '100',
  }];
  const enrichedLine = line({
    markets: {
      ...line().markets,
      'IT_AWAY_GOALS:1.5': { ИТБ2: 8, ИТМ2: 1.1 },
    },
  });
  const snapshots = {
    Fonbet: { status: 'ok', captured_at_utc: NOW, matches: [enrichedLine] },
    Marathonbet: { status: 'source_unavailable', matches: [] },
  };
  const result = coupons.buildArtifact({
    date: DAY, data, models, gated: { generatedAt: NOW, fixtures: [fixture] },
    matching, snapshots, generatedAt: NOW,
  });
  assert.equal(result.artifact.data_status, 'ok');
  assert.ok(result.artifact.prepared_coupons.length > 0);
  const normalized = board.normalizeCoupons(result.artifact);
  assert.equal(normalized.rejected.length, 0);
  assert.equal(normalized.coupons.length, result.artifact.prepared_coupons.length);
  const probabilities = new Set(expectedCandidates.map(row => row.probability));
  for (const coupon of result.artifact.prepared_coupons) {
    assert.equal(coupon.status, 'prepared');
    for (const leg of coupon.legs) assert.ok(probabilities.has(leg.p_model));
  }
});

test('minimum rounded price remains a separate filter after exact EV passes', () => {
  const result = coupons.evaluatePrice(candidate({ label: 'ТБ 2.5', probability: 0.75 }), line({
    markets: { 'TOTAL_GOALS:2.5': { ТБ: 1.375, ТМ: 3 } },
  }));
  assert.equal(result.rejection.reason, 'odds_below_minimum');
  assert.equal(result.rejection.o_min_step, 1.38);
  assert.ok(result.rejection.ev >= 1.03);
});

test('team goals and corner candidates map only to their confirmed statistic and line', () => {
  const team = coupons.evaluatePrice(candidate({ label: 'ИТМ2 1.5' }), line({
    markets: { 'IT_AWAY_GOALS:1.5': { ИТБ2: 3, ИТМ2: 1.5 } },
  }));
  assert.ok(team.leg, team.rejection?.reason);
  assert.equal(team.leg.market_key, 'IT_AWAY_GOALS:1.5');
  const corner = coupons.evaluatePrice(candidate({
    modelLabel: 'Угловые', label: 'ТБ 10.5', probability: 0.75,
  }), line({ markets: { 'TOTAL_CORNERS:10.5': { ТБ: 1.5, ТМ: 3 } } }));
  assert.ok(corner.leg, corner.rejection?.reason);
  assert.equal(corner.leg.market_key, 'TOTAL_CORNERS:10.5');
  const wrongStatistic = coupons.evaluatePrice(candidate({
    modelLabel: 'Угловые', label: 'ТБ 2.5', probability: 0.75,
  }), line());
  assert.equal(wrongStatistic.rejection.reason, 'incomplete_market');
});
