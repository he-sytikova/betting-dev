const test = require('node:test');
const assert = require('node:assert/strict');
const { dayKeyMsk, extractLegs, buildDailyLegs, selectExpresses, buildEventIndex, attachOdds, legOdds, MIN_LEG_PROBABILITY, EXPRESS_LIMIT, MIN_LEG_EV, MAX_COMBINED_ODDS } = require('../express-model.js');

// Minimal schemaVersion-2 goals model (same shape as goals-internal-model tests).
const sampleModel = {
  schemaVersion: 2,
  trainingThrough: '2026-10-29',
  marketLines: { totalGoals: [1.5, 2.5, 3.5], teamGoals: [0.5, 1.5] },
  runtimePolicy: { minimumTeamMatches: 5, maxGoals: 10 },
  leagueNames: { E0: 'England - Premier League' },
  leagueAliases: { 'premier league': 'E0', 'england premier league': 'E0' },
  leagueStats: { E0: { league_goals_avg: 2.7, league_matches_before: 1000 } },
  teams: {
    E0: {
      alpha: { name: 'Alpha', matchesBefore: 100, stats: {} },
      beta: { name: 'Beta', matchesBefore: 100, stats: {} },
      gamma: { name: 'Gamma', matchesBefore: 100, stats: {} },
      delta: { name: 'Delta', matchesBefore: 100, stats: {} },
    },
  },
  models: {
    homeGoals: {
      type: 'poisson',
      features: ['home_advantage', 'league_goals_avg'],
      imputerMedian: [1, 2.7], scalerMean: [1, 2.7], scalerScale: [1, 0.4],
      coefficients: [0.1, 0.05], intercept: 0.2,
    },
    awayGoals: {
      type: 'poisson',
      features: ['home_advantage', 'league_goals_avg'],
      imputerMedian: [1, 2.7], scalerMean: [1, 2.7], scalerScale: [1, 0.4],
      coefficients: [0, 0.05], intercept: -0.1,
    },
  },
};

const future = (iso) => Date.parse(iso); // 2027 dates are safely in the future.

test('dayKeyMsk converts UTC instants to Moscow calendar days', () => {
  assert.equal(dayKeyMsk(Date.parse('2027-03-10T20:59:59Z')), '2027-03-10');
  assert.equal(dayKeyMsk(Date.parse('2027-03-10T21:00:00Z')), '2027-03-11'); // 00:00 МСК next day
  assert.equal(dayKeyMsk(Date.parse('2027-03-10T12:00:00Z')), '2027-03-10');
});

test('extractLegs yields only double-chance and totals legs, never straight 1X2', () => {
  const fixture = { time: future('2027-03-10T17:00:00Z'), league: 'England. Premier League', home: 'Alpha', away: 'Beta' };
  const legs = extractLegs(sampleModel, fixture);
  assert.ok(legs.length > 0);
  const allowed = new Set(['1X', '12', 'X2']);
  for (const leg of legs) {
    if (leg.market === 'doubleChance') assert.ok(allowed.has(leg.label));
    else {
      assert.equal(leg.market, 'total');
      assert.match(leg.label, /^Т[БМ] \d/);
    }
    assert.ok(!['П1', 'X', 'П2'].includes(leg.label));
    assert.ok(leg.probability > 0 && leg.probability < 1);
    assert.equal(leg.home, 'Alpha');
    assert.equal(leg.away, 'Beta');
  }
});

test('extractLegs returns nothing for unknown teams (no invented legs)', () => {
  const fixture = { time: future('2027-03-10T17:00:00Z'), league: 'England. Premier League', home: 'Unknown FC', away: 'Mystery United' };
  assert.deepEqual(extractLegs(sampleModel, fixture), []);
});

test('buildDailyLegs keeps only fixtures of the snapshot day (Moscow time)', () => {
  const predictions = [
    { source: 'x', time: '2027-03-10T17:00:00Z', league: 'England. Premier League', home: 'Alpha', away: 'Beta' },
    { source: 'x', time: '2027-03-10T21:30:00Z', league: 'England. Premier League', home: 'Gamma', away: 'Delta' }, // 00:30 МСК 11-го
    { source: 'x', time: '2027-03-11T15:00:00Z', league: 'England. Premier League', home: 'Gamma', away: 'Delta' },
  ];
  const legs = buildDailyLegs(sampleModel, predictions, '2027-03-10');
  assert.ok(legs.length > 0);
  assert.ok(legs.every(l => l.home === 'Alpha' && l.away === 'Beta'));
});

const leg = (matchKey, probability, time = 0, label = '1X') => ({
  matchKey, time, league: 'L', home: 'H' + matchKey, away: 'A' + matchKey, market: 'doubleChance', label, probability,
});

test('selectExpresses picks pairs of distinct matches by joint probability', () => {
  const legs = [
    leg('m1', 0.90, 1), leg('m1', 0.85, 1, 'ТБ 2.5'), // same match: only strongest competes
    leg('m2', 0.80, 2),
    leg('m3', 0.75, 3),
    leg('m4', 0.70, 4),
  ];
  const top = selectExpresses(legs, { limit: 5 });
  assert.equal(top[0].probability, 0.90 * 0.80);
  assert.deepEqual(top[0].legs.map(l => l.matchKey), ['m1', 'm2']);
  assert.equal(top[0].legs[0].label, '1X'); // strongest leg of m1, not the 0.85 one
  assert.ok(Math.abs(top[0].fairOdds - 1 / (0.72)) < 1e-9);
  // Every express uses two different matches.
  for (const exp of top) assert.notEqual(exp.legs[0].matchKey, exp.legs[1].matchKey);
  // No repeated unordered match pair.
  const pairKeys = top.map(e => e.legs.map(l => l.matchKey).sort().join('|'));
  assert.equal(new Set(pairKeys).size, pairKeys.length);
});

test('selectExpresses respects the probability floor and the limit', () => {
  const legs = [leg('m1', 0.9, 1), leg('m2', 0.8, 2), leg('m3', MIN_LEG_PROBABILITY - 0.01, 3)];
  const top = selectExpresses(legs, { limit: 5 });
  assert.equal(top.length, 1); // m3 falls below the floor
  assert.deepEqual(top[0].legs.map(l => l.matchKey), ['m1', 'm2']);
  const many = [];
  for (let i = 0; i < 10; i++) many.push(leg('m' + i, 0.9 - i * 0.01, i));
  assert.equal(selectExpresses(many).length, EXPRESS_LIMIT);
});

test('selectExpresses returns an empty list when fewer than two matches qualify', () => {
  assert.deepEqual(selectExpresses([leg('m1', 0.95, 1)]), []);
  assert.deepEqual(selectExpresses([leg('m1', 0.95, 1), leg('m2', 0.40, 2)]), []);
  assert.deepEqual(selectExpresses([]), []);
});

// ---- Value mode: bookmaker odds attached to legs ----

const valueLeg = (matchKey, probability, odds, time = 0, label = '1X', market = 'doubleChance') => ({
  matchKey, time, league: 'L', home: 'H' + matchKey, away: 'A' + matchKey, market, label,
  probability, odds, ev: probability * odds,
});

test('selectExpresses in value mode requires positive EV and ranks by joint EV', () => {
  const legs = [
    valueLeg('m1', 0.88, 1.10, 1),          // ev 0.97 — below break-even, excluded
    valueLeg('m2', 0.70, 1.50, 2),          // ev 1.05
    valueLeg('m3', 0.65, 1.70, 3),          // ev 1.105
    valueLeg('m4', 0.80, 1.30, 4),          // ev 1.04
  ];
  const top = selectExpresses(legs, { limit: 5 });
  assert.ok(top.length > 0);
  for (const exp of top) {
    assert.ok(Number.isFinite(exp.combinedOdds));
    assert.ok(exp.legs.every(l => l.ev >= MIN_LEG_EV));
  }
  // Best pair by joint EV: m3 (1.105) x m2 (1.05) = 1.16025.
  assert.deepEqual(top[0].legs.map(l => l.matchKey), ['m2', 'm3']);
  assert.ok(Math.abs(top[0].ev - 1.105 * 1.05) < 1e-9);
  assert.ok(Math.abs(top[0].combinedOdds - 1.70 * 1.50) < 1e-9);
});

test('value mode prefers the higher-EV leg of a match over the safer one', () => {
  const legs = [
    valueLeg('m1', 0.90, 1.05, 1, '1X'),    // ev 0.945
    valueLeg('m1', 0.62, 1.80, 1, 'X2'),    // ev 1.116 — best for m1
    valueLeg('m2', 0.70, 1.50, 2),          // ev 1.05
  ];
  const top = selectExpresses(legs, { limit: 5 });
  assert.equal(top.length, 1);
  assert.equal(top[0].legs[0].label, 'X2');
});

test('value mode drops pairs whose combined bookmaker odds exceed the cap', () => {
  const legs = [
    valueLeg('m1', 0.62, 3.00, 1),   // ev 1.86
    valueLeg('m2', 0.61, 2.50, 2),   // ev 1.525, pair odds 7.5 > cap
    valueLeg('m3', 0.65, 1.20, 3),   // ev 0.78 — excluded
  ];
  const top = selectExpresses(legs, { limit: 5, maxCombinedOdds: MAX_COMBINED_ODDS });
  assert.deepEqual(top, []);
});

test('probability mode is unchanged when no leg carries odds', () => {
  const legs = [leg('m1', 0.90, 1), leg('m2', 0.80, 2), leg('m3', 0.75, 3)];
  const top = selectExpresses(legs, { limit: 5 });
  assert.deepEqual(top[0].legs.map(l => l.matchKey), ['m1', 'm2']);
  assert.equal(top[0].combinedOdds, null);
  assert.equal(top[0].ev, null);
});

test('buildEventIndex indexes only Euro365 rows and stringifies ids', () => {
  const predictions = [
    { source: 'Euro365', eventId: 42, time: '2027-03-10T17:00:00Z', league: 'England. Premier League', home: 'Alpha', away: 'Beta' },
    { source: 'SStats', eventId: 777, time: '2027-03-10T17:00:00Z', league: 'England. Premier League', home: 'Alpha', away: 'Beta' },
    { source: 'Euro365', time: '2027-03-10T18:00:00Z', league: 'England. Premier League', home: 'Gamma', away: 'Delta' }, // no id
  ];
  const index = buildEventIndex(sampleModel, predictions);
  const keyAlphaBeta = [...index.entries()].find(([, id]) => id === '42');
  assert.ok(keyAlphaBeta, 'Euro365 row must be indexed');
  assert.ok(![...index.values()].includes('777'), 'SStats ids never enter the Euro365 index');
  assert.equal(index.size, 1);
});

test('attachOdds maps double chance and totals legs to raw decimal prices', () => {
  const legs = [
    { matchKey: 'm1', market: 'doubleChance', label: '1X', probability: 0.8 },
    { matchKey: 'm1', market: 'total', label: 'ТБ 2.5', probability: 0.7 },
    { matchKey: 'm1', market: 'total', label: 'ТМ 3.5', probability: 0.75 },
    { matchKey: 'm2', market: 'doubleChance', label: '12', probability: 0.9 }, // no event
  ];
  const oddsData = {
    events: {
      '42': { markets: { doubleChance: { '1X': 1.28 }, totals: { '2.5': { over: 2.10, under: 1.70 } } } },
    },
  };
  attachOdds(legs, oddsData, new Map([['m1', '42']]));
  assert.equal(legs[0].odds, 1.28);
  assert.ok(Math.abs(legs[0].ev - 0.8 * 1.28) < 1e-9);
  assert.equal(legs[1].odds, 2.10);
  assert.equal(legs[2].odds, undefined, 'missing totals line stays without odds');
  assert.equal(legs[3].odds, undefined, 'unmatched event stays without odds');
});

test('legOdds rejects invalid or missing prices instead of inventing them', () => {
  assert.equal(legOdds({ market: 'doubleChance', label: '1X' }, null), null);
  assert.equal(legOdds({ market: 'doubleChance', label: '1X' }, { doubleChance: { '12': 1.2 } }), null);
  assert.equal(legOdds({ market: 'doubleChance', label: '1X' }, { doubleChance: { '1X': 1.0 } }), null);
  assert.equal(legOdds({ market: 'total', label: 'ТБ 2.5' }, { totals: { '2.5': { over: 1.95, under: 1.85 } } }), 1.95);
  assert.equal(legOdds({ market: 'total', label: 'ТМ 2.5' }, { totals: { '2.5': { over: 1.95, under: 1.85 } } }), 1.85);
  assert.equal(legOdds({ market: 'total', label: 'ТБ 9.5' }, { totals: { '2.5': { over: 1.95 } } }), null);
});

test('value mode admits the neutral zone 0.97-1.00 but not below it', () => {
  const legs = [
    valueLeg('m1', 0.62, 1.58, 1),   // ev 0.9796 — neutral zone, admitted
    valueLeg('m2', 0.70, 1.50, 2),   // ev 1.05
    valueLeg('m3', 0.60, 1.60, 3),   // ev 0.96 — below the floor, excluded
  ];
  const top = selectExpresses(legs, { limit: 5 });
  assert.equal(top.length, 1);
  assert.deepEqual(top[0].legs.map(l => l.matchKey), ['m1', 'm2']);
  assert.ok(Math.abs(top[0].combinedOdds - 1.58 * 1.50) < 1e-9);
});
