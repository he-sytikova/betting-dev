const test = require('node:test');
const assert = require('node:assert/strict');
const { poissonOver, predict, resolveLeague, resolveTeam, predictLambda, norm, uniqueFixtures } = require('../internal-model.js');

// Minimal synthetic model that mirrors the schemaVersion 2 export format.
const sampleModel = {
  schemaVersion: 2,
  trainingThrough: '2026-10-29',
  leagueNames: { E0: 'England - Premier League' },
  leagueAliases: { 'premier league': 'E0', 'england premier league': 'E0' },
  leagueStats: { E0: { league_corners_avg: 10.2, league_cards_avg: 3.9, league_goals_avg: 2.7, league_fouls_avg: 21, league_matches_before: 1000 } },
  runtimePolicy: { minimumTeamMatches: 5 },
  teams: {
    E0: {
      newcastle: { name: 'Newcastle', matchesBefore: 100, stats: { team_for_avg10: 5.5, team_against_avg10: 4.5, card_team_for_avg10: 2.0, card_team_against_avg10: 1.8 } },
      everton: { name: 'Everton', matchesBefore: 100, stats: { team_for_avg10: 4.8, team_against_avg10: 5.4, card_team_for_avg10: 2.2, card_team_against_avg10: 2.0 } },
      newcomer: { name: 'Newcomer', matchesBefore: 2, stats: {} },
    },
  },
  models: {
    corners: {
      type: 'poisson',
      features: ['home_team_for_avg10', 'away_team_for_avg10', 'league_corners_avg', 'home_advantage', 'referee_available', 'referee_available__missing', 'league__E0'],
      imputerMedian: [5, 5, 10, 1, 1, 0, 0],
      scalerMean: [5, 5, 10, 1, 1, 0.1, 0.5],
      scalerScale: [1, 1, 0.5, 1, 0.3, 0.3, 0.5],
      coefficients: [0.05, 0.04, 0.02, 0, 0.01, 0, 0],
      intercept: 2.2,
      lines: [8.5, 9.5],
    },
    yellowCards: {
      type: 'poisson',
      features: ['home_card_team_for_avg10', 'away_card_team_for_avg10', 'league_cards_avg', 'home_advantage', 'referee_yellows_avg10', 'referee_yellows_avg10__missing', 'league__E0'],
      imputerMedian: [2, 2, 3.8, 1, 3.5, 0, 0],
      scalerMean: [2, 2, 3.8, 1, 3.5, 0.1, 0.5],
      scalerScale: [0.5, 0.5, 0.6, 1, 0.8, 0.3, 0.5],
      coefficients: [0.03, 0.03, 0.05, 0, 0.02, 0, 0],
      intercept: 1.3,
      lines: [2.5, 3.5],
    },
  },
};

test('poissonOver matches a hand-computed Poisson tail', () => {
  // P(X > 9) for lambda = 10: 1 - sum_{k=0..9} poisson(k;10) = 0.5420703...
  assert.ok(Math.abs(poissonOver(10, 9.5) - 0.5420703) < 1e-6);
  // Lower lines must have a higher over probability.
  assert.ok(poissonOver(10, 8.5) > poissonOver(10, 10.5));
});

test('predictLambda reproduces the linear predictor with scaling and imputation', () => {
  const spec = sampleModel.models.corners;
  const ctx = { home_team_for_avg10: 5.5, away_team_for_avg10: 4.8, league_corners_avg: 10.2, home_advantage: 1 };
  // referee_available is missing -> median 1, indicator 1.
  const expected = Math.exp(2.2
    + 0.05 * ((5.5 - 5) / 1) + 0.04 * ((4.8 - 5) / 1) + 0.02 * ((10.2 - 10) / 0.5)
    + 0 * ((1 - 1) / 1) + 0.01 * ((1 - 1) / 0.3) + 0 * ((1 - 0.1) / 0.3) + 0 * ((1 - 0.5) / 0.5));
  assert.ok(Math.abs(predictLambda(spec, ctx, 'E0') - expected) < 1e-9);
});

test('resolves leagues and teams through aliases', () => {
  assert.equal(resolveLeague(sampleModel, 'England. Premier League'), 'E0');
  assert.equal(resolveLeague(sampleModel, 'La Liga'), null); // not present in the sample model
  assert.equal(resolveLeague(sampleModel, 'Premier League'), 'E0');
  const resolved = resolveTeam(sampleModel, 'E0', 'Newcastle United FC');
  assert.ok(resolved);
  assert.equal(resolved.key, 'newcastle');
});

test('scores a fully matched fixture and keeps line order', () => {
  const result = predict(sampleModel, 'Premier League', 'Newcastle United', 'Everton FC');
  assert.ok(result);
  assert.equal(result.homeModel, 'Newcastle');
  assert.deepEqual(result.corners.lines.map(x => x.line), [8.5, 9.5]);
  for (const market of ['corners', 'yellowCards']) {
    const overs = result[market].lines.map(x => x.over);
    assert.ok(overs.every(p => p > 0 && p < 1));
    assert.ok(overs[0] > overs[1]);
    // Half lines cannot push: under is exactly the complement of over.
    for (const x of result[market].lines) {
      assert.ok(Math.abs(x.over + x.under - 1) < 1e-12);
    }
  }
});

test('never invents probabilities for unknown leagues, teams or thin history', () => {
  assert.equal(predict(sampleModel, 'MLS', 'Newcastle United', 'Everton'), null);
  assert.equal(predict(sampleModel, 'Premier League', 'Newcastle United', 'Inter Miami'), null);
  assert.equal(predict(sampleModel, 'Premier League', 'Newcastle United', 'Newcomer'), null);
  assert.equal(predict(null, 'Premier League', 'Newcastle', 'Everton'), null);
});

test('unique substring fallback is applied only when unambiguous', () => {
  const resolved = resolveTeam(sampleModel, 'E0', 'Everton FC');
  assert.equal(resolved.key, 'everton');
  assert.equal(resolveTeam(sampleModel, 'E0', 'New'), null); // matches two teams
});

test('norm strips club prefixes like the export normalizer', () => {
  assert.equal(norm('FC Koln'), 'koln');
  assert.equal(norm('RC Deportivo La Coruña'), 'rc deportivo la coruna');
});

test('uniqueFixtures collapses provider naming variants via model identity', () => {
  const future = Date.now() + 3600e3;
  const iso = new Date(future).toISOString();
  const fixtures = uniqueFixtures([
    { time: iso, league: 'Premier League', home: 'Newcastle United', away: 'Everton' },
    { time: iso, league: 'England. Premier League', home: 'Newcastle', away: 'Everton FC' },
    { time: iso, league: 'MLS', home: 'Inter Miami', away: 'LA Galaxy' },
  ], sampleModel);
  // The two Premier League records are one match; the MLS fixture stays as-is.
  assert.equal(fixtures.length, 2);
  assert.equal(fixtures.filter(f => f.league !== 'MLS').length, 1);
});

test('extended league set: sponsored names resolve, lookalike divisions stay null', () => {
  const extended = {
    leagueNames: {},
    leagueAliases: {
      'liga portugal betclic': 'P1', 'portugal primeira liga': 'P1',
      'trendyol super lig': 'T1', 'turkey superliga': 'T1', 'turkey super lig': 'T1',
      'greece superleague': 'G1', 'segunda division': 'SP2', 'ligue 2': 'F2',
      'belgium pro league': 'B1', 'scotland championship': 'SC1',
    },
    leagueStats: {},
    teams: {
      P1: { guimaraes: { name: 'Guimaraes', matchesBefore: 100, stats: {} } },
      B1: { 'st gilloise': { name: 'St. Gilloise', matchesBefore: 100, stats: {} } },
      T1: { buyuksehyr: { name: 'Buyuksehyr', matchesBefore: 100, stats: {} } },
      SC1: { 'raith rvs': { name: 'Raith Rvs', matchesBefore: 100, stats: {} } },
    },
  };
  assert.equal(resolveLeague(extended, 'Liga Portugal Betclic'), 'P1');
  assert.equal(resolveLeague(extended, 'Trendyol Super Lig'), 'T1');
  assert.equal(resolveLeague(extended, 'Turkey. SuperLiga'), 'T1');
  // Second divisions with lookalike names must NOT resolve to our leagues.
  assert.equal(resolveLeague(extended, 'Greece. SuperLeague 2'), null);
  assert.equal(resolveLeague(extended, 'Liga Portugal 2'), null);
  assert.equal(resolveLeague(extended, 'Senegal. Ligue 2'), null);
  assert.equal(resolveLeague(extended, 'Turkey. TFF 1. Lig'), null);
  // Curated team aliases for the extended leagues.
  assert.equal(resolveTeam(extended, 'P1', 'Vitória SC').key, 'guimaraes');
  assert.equal(resolveTeam(extended, 'B1', 'Union SG').key, 'st gilloise');
  assert.equal(resolveTeam(extended, 'T1', 'İstanbul Başakşehir').key, 'buyuksehyr');
  assert.equal(resolveTeam(extended, 'SC1', 'Raith Rovers').key, 'raith rvs');
});

// --- NB2 distribution and line calibration (stage 2, 2026-11) ---
const { nbOver, overProbability, calibrate } = require('../internal-model.js');

test('nbOver matches scipy nbinom reference values', () => {
  // References: 1 - scipy.stats.nbinom.cdf(floor(line), 1/alpha, (1/alpha)/(1/alpha+lambda))
  const cases = [
    [10.0, 9.5, 0.015326579065490254, 0.533229],
    [25.0, 24.5, 0.00979110038167496, 0.518026],
    [3.0, 2.5, 0.05, 0.561210],
    [1.2, 0.5, 0.2, 0.658892],
  ];
  for (const [lambda, line, alpha, expected] of cases) {
    assert.ok(Math.abs(nbOver(lambda, line, alpha) - expected) < 1e-5,
      `nbOver(${lambda}, ${line}, ${alpha}) should be ${expected}, got ${nbOver(lambda, line, alpha)}`);
  }
});

test('overProbability picks NB2 only for nb2 specs', () => {
  const nbSpec = { type: 'nb2', nbAlpha: 0.05 };
  const poissonSpec = { type: 'poisson' };
  assert.equal(overProbability(nbSpec, 3.0, 2.5), nbOver(3.0, 2.5, 0.05));
  assert.equal(overProbability(poissonSpec, 3.0, 2.5), poissonOver(3.0, 2.5));
  // NB2 with positive alpha must widen tails vs Poisson at an upper line.
  assert.ok(nbOver(3.0, 4.5, 0.2) > poissonOver(3.0, 4.5));
});

test('calibrate interpolates linearly and clamps outside knots', () => {
  const knots = { x: [0.2, 0.5, 0.8], y: [0.15, 0.5, 0.9] };
  assert.equal(calibrate(0.1, knots), 0.15);
  assert.equal(calibrate(0.9, knots), 0.9);
  assert.ok(Math.abs(calibrate(0.65, knots) - 0.7) < 1e-12);
  assert.equal(calibrate(0.5, null), 0.5);
  assert.equal(calibrate(0.5, { x: [], y: [] }), 0.5);
});

test('predict uses NB2 tail and applies embedded line calibrators', () => {
  const model = JSON.parse(JSON.stringify(sampleModel));
  model.models.corners.type = 'nb2';
  model.models.corners.nbAlpha = 0.02;
  model.models.corners.calibration = { '9.5': { x: [0, 1], y: [0.1, 0.9] } };
  const prediction = predict(model, 'Premier League', 'Newcastle', 'Everton');
  assert.equal(prediction.corners.distribution, 'nb2');
  const lambda = prediction.corners.lambda;
  for (const entry of prediction.corners.lines) {
    const raw = nbOver(lambda, entry.line, 0.02);
    const knots = model.models.corners.calibration[String(entry.line)];
    const expected = knots ? calibrate(raw, knots) : raw;
    assert.ok(Math.abs(entry.over - expected) < 1e-9, `line ${entry.line}: ${entry.over} != ${expected}`);
    assert.ok(Math.abs(entry.under - (1 - entry.over)) < 1e-12);
  }
  // The NB2 prediction must differ from what Poisson would give.
  const poissonLine = prediction.corners.lines.find(x => x.line === 9.5);
  assert.ok(Math.abs(poissonLine.over - poissonOver(lambda, 9.5)) > 1e-6);
});

test('predict stays Poisson and uncalibrated without nb2/calibration fields', () => {
  const prediction = predict(sampleModel, 'Premier League', 'Newcastle', 'Everton');
  assert.equal(prediction.corners.distribution, 'poisson');
  const lambda = prediction.corners.lambda;
  for (const entry of prediction.corners.lines) {
    assert.ok(Math.abs(entry.over - poissonOver(lambda, entry.line)) < 1e-9);
  }
});
