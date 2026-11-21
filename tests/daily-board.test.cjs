const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const board = require('../daily-board.js');
const goalsApi = require('../goals-internal-model.js');

const DAY = '2099-01-01';
const NOW = Date.parse(`${DAY}T12:00:00Z`);

function goalsModel(homeLambda = 3, awayLambda = 0.2) {
  return {
    schemaVersion: 2,
    leagueAliases: { 'england premier league': 'E0' },
    leagueNames: { E0: 'England. Premier League' },
    leagueStats: { E0: {} },
    teams: {
      E0: {
        newcastle: { name: 'Newcastle', stats: {}, matchesBefore: 100 },
        everton: { name: 'Everton', stats: {}, matchesBefore: 100 },
      },
    },
    features: [],
    imputerMedian: [],
    scalerMean: [],
    scalerScale: [],
    models: {
      homeGoals: { type: 'poisson', features: [], coefficients: [], intercept: Math.log(homeLambda) },
      awayGoals: { type: 'poisson', features: [], coefficients: [], intercept: Math.log(awayLambda) },
    },
    marketLines: { totalGoals: [1.5, 2.5], teamGoals: [0.5, 1.5] },
    runtimePolicy: { minimumTeamMatches: 5, maxGoals: 12 },
  };
}

function clubStatsModel(models) {
  const specs = {};
  for (const [market, config] of Object.entries(models)) {
    specs[market] = {
      type: 'poisson', features: [], coefficients: [],
      intercept: Math.log(config.lambda), lines: config.lines,
    };
  }
  return {
    schemaVersion: 2,
    leagueAliases: { 'england premier league': 'E0' },
    leagueNames: { E0: 'England. Premier League' },
    leagueStats: { E0: {} },
    teams: {
      E0: {
        newcastle: { name: 'Newcastle', stats: {}, matchesBefore: 100 },
        everton: { name: 'Everton', stats: {}, matchesBefore: 100 },
      },
    },
    models: specs,
    runtimePolicy: { minimumTeamMatches: 5 },
  };
}

function loadNationsModel() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'nt_model.json'), 'utf8'));
}

function clubFixture(time = `${DAY}T18:00:00Z`) {
  return { time, league: 'England. Premier League', home: 'Newcastle United', away: 'Everton FC' };
}

function leg(overrides = {}) {
  return {
    home: 'Newcastle', away: 'Everton', league: 'Premier League',
    time: `${DAY}T18:00:00Z`, label: '1X', probability: 0.8,
    odds: 1.4, fair: 0.75, delta_pp: 5, ev: 1.12,
    bookmaker: 'Fonbet', ...overrides,
  };
}

test('Moscow day changes at 21:00 UTC, not at browser-local midnight', () => {
  assert.equal(board.dayKeyMsk(Date.parse('2026-11-16T20:59:59Z')), '2026-11-16');
  assert.equal(board.dayKeyMsk(Date.parse('2026-11-16T21:00:00Z')), '2026-11-17');
  assert.equal(board.dayKeyMsk('invalid'), null);
});

test('minimumOdds uses EV 1.03 and rounds upward to a 0.01 step', () => {
  const regular = board.minimumOdds(0.8);
  assert.ok(Math.abs(regular.exact - 1.2875) < 1e-12);
  assert.equal(regular.step, 1.29);
  const certain = board.minimumOdds(1);
  assert.ok(Math.abs(certain.exact - 1.03) < 1e-12);
  assert.equal(certain.step, 1.03);
  const boundary = board.minimumOdds(1.03 / 1.47);
  assert.ok(Math.abs(boundary.exact - 1.47) < 1e-12);
  assert.equal(boundary.step, 1.47);
  assert.equal(board.minimumOdds(0.7).step, 1.48);
});

test('candidate admission uses the exact 75% model-probability boundary', () => {
  assert.equal(board.MIN_PROBABILITY, 0.75);
  const fixture = {
    time: Date.parse(`${DAY}T18:00:00Z`), key: 'model|E0|newcastle|everton',
    league: 'England. Premier League', home: 'Newcastle', away: 'Everton',
  };
  assert.equal(board.candidate(fixture, {}, 'Голы', '1X', 0.749999999999), null);
  const accepted = board.candidate(fixture, {}, 'Голы', '1X', 0.75);
  assert.ok(accepted);
  assert.equal(accepted.probability, 0.75);
  assert.equal(accepted.oMinStep, 1.38);
});

test('modelFixtures keeps future current-day matches and deduplicates by model identity', () => {
  const fixtures = board.modelFixtures(goalsApi, goalsModel(), [
    clubFixture(`${DAY}T19:00:00Z`),
    { ...clubFixture(`${DAY}T18:00:00Z`), home: 'Newcastle', away: 'Everton' },
    clubFixture(`${DAY}T11:59:59Z`),
    clubFixture('2099-01-02T18:00:00Z'),
    { ...clubFixture(), home: '', away: 'Everton' },
    { ...clubFixture(), time: 'not-a-date' },
  ], DAY, NOW);
  assert.equal(fixtures.length, 1);
  assert.equal(fixtures[0].time, Date.parse(`${DAY}T18:00:00Z`));
  assert.equal(fixtures[0].home, 'Newcastle');
});

test('nationsFixtures rejects unknown teams and competitions outside UEFA Nations League', () => {
  const nations = loadNationsModel();
  const fixtures = board.nationsFixtures(nations, [
    { time: `${DAY}T20:00:00Z`, league: 'UEFA Nations League', home: 'Spain', away: 'France' },
    { time: `${DAY}T20:00:00Z`, league: 'UEFA Nations League', home: 'Spain', away: 'Atlantis XI' },
    { time: `${DAY}T20:00:00Z`, league: 'UEFA Champions League', home: 'Spain', away: 'France' },
  ], DAY, NOW);
  assert.equal(fixtures.length, 1);
  assert.equal(fixtures[0].home, 'Spain');
});

test('buildCandidates covers all active model families and preserves market lines', () => {
  const data = {
    date: DAY,
    predictions: [
      clubFixture(),
      { time: `${DAY}T20:00:00Z`, league: 'UEFA Nations League', home: 'Spain', away: 'France' },
      { time: `${DAY}T18:00:00Z`, league: 'Unknown League', home: 'Unknown A', away: 'Unknown B' },
    ],
  };
  const rows = board.buildCandidates(data, {
    goals: goalsModel(),
    cornersCards: clubStatsModel({
      corners: { lambda: 12, lines: [8.5, 10.5] },
      yellowCards: { lambda: 2.5, lines: [2.5, 3.5] },
    }),
    stats: clubStatsModel({
      fouls: { lambda: 25, lines: [20.5] },
      shots: { lambda: 30, lines: [24.5] },
      shotsOnTarget: { lambda: 12, lines: [8.5] },
    }),
    nations: loadNationsModel(),
  }, NOW);

  assert.deepEqual(new Set(rows.map(row => row.modelLabel)), new Set([
    'Голы', 'Угловые', 'ЖК', 'Фолы', 'Удары', 'Удары в створ', 'Лига наций',
  ]));
  for (const label of ['П1', '1X', 'ТБ 1.5', 'ИТБ1 0.5', 'ИТМ2 0.5']) {
    assert.ok(rows.some(row => row.modelLabel === 'Голы' && row.label === label), label);
  }
  assert.ok(rows.some(row => row.modelLabel === 'Угловые' && row.label === 'ТБ 8.5'));
  assert.ok(rows.some(row => row.modelLabel === 'ЖК' && row.label === 'ТМ 3.5'));
  assert.ok(rows.some(row => row.modelLabel === 'Лига наций'));
  assert.ok(rows.every(row => row.probability >= board.MIN_PROBABILITY && row.probability <= 1));
  assert.ok(rows.every(row => row.lineStatus === 'not_checked'));
  assert.ok(rows.every(row => row.oMinStep >= row.oMinExact - 1e-12));
  assert.ok(rows.every(row => row.time > NOW && board.dayKeyMsk(row.time) === DAY));
  assert.ok(rows.every(row => !String(row.home).includes('Unknown')));
});

test('groupCandidatesByMatch collapses markets into exact match events without changing their order', () => {
  const first = {
    time: Date.parse(`${DAY}T18:00:00Z`), home: 'Newcastle', away: 'Everton',
    league: 'England. Premier League', modelKey: 'goals|E0|newcastle|everton', label: '1X',
  };
  const rows = [
    first,
    { ...first, modelKey: 'corners|E0|newcastle|everton', label: 'ТБ 8.5' },
    { ...first, time: Date.parse(`${DAY}T20:00:00Z`), label: 'X2' },
  ];
  const groups = board.groupCandidatesByMatch(rows);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups.map(group => group.rows.length), [2, 1]);
  assert.deepEqual(groups[0].rows.map(row => row.label), ['1X', 'ТБ 8.5']);
  assert.equal(groups[0].home, 'Newcastle');
  assert.equal(groups[1].time, Date.parse(`${DAY}T20:00:00Z`));
  assert.deepEqual(board.groupCandidatesByMatch([]), []);
  assert.equal(rows.length, 3);
});

test('buildCandidates returns no candidates for a malformed snapshot', () => {
  assert.deepEqual(board.buildCandidates({}, {}, NOW), []);
  assert.deepEqual(board.buildCandidates({ date: DAY, predictions: null }, {}, NOW), []);
});

test('normalizeCoupon derives single-coupon metrics and always marks it prepared', () => {
  const coupon = board.normalizeCoupon({ ...leg(), status: 'placed' }, 0);
  assert.equal(coupon.type, 'single');
  assert.equal(coupon.bookmaker, 'Fonbet');
  assert.equal(coupon.odds, 1.4);
  assert.equal(coupon.probability, 0.8);
  assert.equal(coupon.ev, 1.12);
  assert.equal(coupon.status, 'prepared');
});

test('normalizeCoupon derives double odds, probability and EV from same-bookmaker legs', () => {
  const coupon = board.normalizeCoupon({
    coupon_id: 'double-1',
    legs: [leg(), leg({ home: 'Spain', away: 'France', probability: 0.75, odds: 1.5, ev: null })],
  }, 0);
  assert.equal(coupon.id, 'double-1');
  assert.equal(coupon.type, 'double');
  assert.ok(Math.abs(coupon.odds - 2.1) < 1e-12);
  assert.ok(Math.abs(coupon.probability - 0.6) < 1e-12);
  assert.ok(Math.abs(coupon.ev - 1.26) < 1e-12);
  assert.equal(coupon.status, 'prepared');
});

test('normalizeCoupon rejects a double assembled from different bookmakers', () => {
  const coupon = board.normalizeCoupon({
    legs: [leg(), leg({ home: 'Spain', away: 'France', bookmaker: 'Marathonbet' })],
  }, 0);
  assert.equal(coupon, null);
});

test('normalizeCoupons skips malformed records and reports rejected indexes', () => {
  const result = board.normalizeCoupons({ prepared_coupons: [
    leg(),
    { legs: [leg(), leg({ bookmaker: 'Marathonbet' })] },
    { home: 'Only home', odds: 1.5, bookmaker: 'Fonbet' },
    { legs: [leg(), leg(), leg()] },
  ] });
  assert.equal(result.coupons.length, 1);
  assert.deepEqual(result.rejected, [1, 2, 3]);
});

test('couponEmptyMessage distinguishes processing states and an empty processed day', () => {
  assert.match(board.couponEmptyMessage({ data_status: 'not_processed' }), /ещё не обработаны/);
  assert.match(board.couponEmptyMessage({ data_status: 'source_unavailable' }), /недоступен/);
  assert.match(board.couponEmptyMessage({ data_status: 'no_line_match' }), /не сопоставлены/);
  assert.match(board.couponEmptyMessage({ data_status: 'ok' }), /пропуск/);
  assert.match(board.couponEmptyMessage({ data_status: 'stale_snapshot' }), /не считается проставленным/);
});
