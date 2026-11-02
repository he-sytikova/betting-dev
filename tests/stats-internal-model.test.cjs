const test = require('node:test');
const assert = require('node:assert/strict');
const { poissonOver, predict, resolveLeague, resolveTeam, predictLambda, uniqueFixtures } = require('../stats-internal-model.js');

// Minimal synthetic model that mirrors the schemaVersion 2 stats export format.
const sampleModel = {
  schemaVersion: 2,
  trainingThrough: '2026-10-29',
  leagueNames: { E0: 'England - Premier League' },
  leagueAliases: { 'premier league': 'E0', 'england premier league': 'E0' },
  leagueStats: { E0: { league_corners_avg: 10.2, league_cards_avg: 3.9, league_goals_avg: 2.7, league_fouls_avg: 22, league_matches_before: 1000 } },
  runtimePolicy: { minimumTeamMatches: 5 },
  teams: {
    E0: {
      newcastle: { name: 'Newcastle', matchesBefore: 100, stats: { foul_team_for_avg10: 11.5, foul_team_against_avg10: 10.5, shots_team_for_avg10: 13.0, shots_team_against_avg10: 11.0, shots_on_target_team_for_avg10: 4.8 } },
      everton: { name: 'Everton', matchesBefore: 100, stats: { foul_team_for_avg10: 10.8, foul_team_against_avg10: 11.2, shots_team_for_avg10: 10.5, shots_team_against_avg10: 13.5, shots_on_target_team_for_avg10: 3.6 } },
      newcomer: { name: 'Newcomer', matchesBefore: 2, stats: {} },
    },
  },
  models: {
    fouls: {
      type: 'poisson',
      features: ['home_foul_team_for_avg10', 'away_foul_team_for_avg10', 'league_fouls_avg', 'home_advantage', 'referee_fouls_avg10', 'referee_fouls_avg10__missing', 'league__E0'],
      imputerMedian: [11, 11, 22, 1, 22, 0, 0],
      scalerMean: [11, 11, 22, 1, 22, 0.1, 0.5],
      scalerScale: [2, 2, 3, 1, 4, 0.3, 0.5],
      coefficients: [0.04, 0.04, 0.02, 0, 0.03, 0, 0],
      intercept: 3.1,
      lines: [22.5, 24.5],
    },
    shots: {
      type: 'poisson',
      features: ['home_shots_team_for_avg10', 'away_shots_team_for_avg10', 'home_shots_team_against_avg10', 'away_shots_team_against_avg10', 'home_advantage', 'league__E0'],
      imputerMedian: [12, 12, 12, 12, 1, 0],
      scalerMean: [12, 12, 12, 12, 1, 0.5],
      scalerScale: [3, 3, 3, 3, 1, 0.5],
      coefficients: [0.05, 0.05, 0.03, 0.03, 0, 0],
      intercept: 3.0,
      lines: [21.5, 23.5],
    },
    shotsOnTarget: {
      type: 'poisson',
      features: ['home_shots_on_target_team_for_avg10', 'away_shots_on_target_team_for_avg10', 'home_advantage', 'league__E0'],
      imputerMedian: [4, 4, 1, 0],
      scalerMean: [4, 4, 1, 0.5],
      scalerScale: [1.5, 1.5, 1, 0.5],
      coefficients: [0.05, 0.05, 0, 0],
      intercept: 2.1,
      lines: [8.5, 9.5],
    },
  },
};

test('predict scores all three stats markets with line order and under complement', () => {
  const result = predict(sampleModel, 'Premier League', 'Newcastle United', 'Everton FC');
  assert.ok(result);
  assert.equal(result.homeModel, 'Newcastle');
  for (const market of ['fouls', 'shots', 'shotsOnTarget']) {
    assert.ok(result[market], market + ' missing');
    assert.deepEqual(result[market].lines.map(x => x.line), sampleModel.models[market].lines);
    const overs = result[market].lines.map(x => x.over);
    assert.ok(overs.every(p => p > 0 && p < 1));
    assert.ok(overs[0] > overs[1], market + ' over must decrease with the line');
    for (const x of result[market].lines) {
      assert.ok(Math.abs(x.over + x.under - 1) < 1e-12, market + ' under must complement over');
    }
  }
});

test('predictLambda reproduces the linear predictor with scaling and imputation', () => {
  const spec = sampleModel.models.fouls;
  const ctx = { home_foul_team_for_avg10: 11.5, away_foul_team_for_avg10: 10.8, league_fouls_avg: 22, home_advantage: 1 };
  // referee_fouls_avg10 is missing -> median 22, indicator 1.
  const expected = Math.exp(3.1
    + 0.04 * ((11.5 - 11) / 2) + 0.04 * ((10.8 - 11) / 2) + 0.02 * ((22 - 22) / 3)
    + 0 * ((1 - 1) / 1) + 0.03 * ((22 - 22) / 4) + 0 * ((1 - 0.1) / 0.3) + 0 * ((1 - 0.5) / 0.5));
  assert.ok(Math.abs(predictLambda(spec, ctx, 'E0') - expected) < 1e-9);
});

test('never invents probabilities for unknown leagues, teams or thin history', () => {
  assert.equal(predict(sampleModel, 'MLS', 'Newcastle United', 'Everton'), null);
  assert.equal(predict(sampleModel, 'Premier League', 'Newcastle United', 'Inter Miami'), null);
  assert.equal(predict(sampleModel, 'Premier League', 'Newcastle United', 'Newcomer'), null);
  assert.equal(predict(null, 'Premier League', 'Newcastle', 'Everton'), null);
});

test('resolves leagues and teams through aliases', () => {
  assert.equal(resolveLeague(sampleModel, 'Premier League'), 'E0');
  assert.equal(resolveLeague(sampleModel, 'La Liga'), null);
  const resolved = resolveTeam(sampleModel, 'E0', 'Newcastle United FC');
  assert.ok(resolved);
  assert.equal(resolved.key, 'newcastle');
});

test('uniqueFixtures collapses provider naming variants via model identity', () => {
  const future = Date.now() + 3600e3;
  const iso = new Date(future).toISOString();
  const fixtures = uniqueFixtures([
    { time: iso, league: 'Premier League', home: 'Newcastle United', away: 'Everton' },
    { time: iso, league: 'England. Premier League', home: 'Newcastle', away: 'Everton FC' },
    { time: iso, league: 'MLS', home: 'Inter Miami', away: 'LA Galaxy' },
  ], sampleModel);
  assert.equal(fixtures.length, 2);
  assert.equal(fixtures.filter(f => f.league !== 'MLS').length, 1);
});

test('poissonOver matches a hand-computed tail and is monotonic in lambda', () => {
  // P(X > 24) for lambda = 25: 1 - sum_{k=0..24} poisson(k;25) = 0.5264...
  assert.ok(Math.abs(poissonOver(25, 24.5) - 0.5264) < 1e-3);
  assert.ok(poissonOver(26, 24.5) > poissonOver(24, 24.5));
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
const { nbOver, overProbability, calibrate } = require('../stats-internal-model.js');

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
  model.models.fouls.type = 'nb2';
  model.models.fouls.nbAlpha = 0.02;
  model.models.fouls.calibration = { '24.5': { x: [0, 1], y: [0.1, 0.9] } };
  const prediction = predict(model, 'Premier League', 'Newcastle United', 'Everton FC');
  assert.equal(prediction.fouls.distribution, 'nb2');
  const lambda = prediction.fouls.lambda;
  for (const entry of prediction.fouls.lines) {
    const raw = nbOver(lambda, entry.line, 0.02);
    const knots = model.models.fouls.calibration[String(entry.line)];
    const expected = knots ? calibrate(raw, knots) : raw;
    assert.ok(Math.abs(entry.over - expected) < 1e-9, `line ${entry.line}: ${entry.over} != ${expected}`);
    assert.ok(Math.abs(entry.under - (1 - entry.over)) < 1e-12);
  }
  const poissonLine = prediction.fouls.lines.find(x => x.line === 24.5);
  assert.ok(Math.abs(poissonLine.over - poissonOver(lambda, 24.5)) > 1e-6);
});

test('predict stays Poisson and uncalibrated without nb2/calibration fields', () => {
  const prediction = predict(sampleModel, 'Premier League', 'Newcastle United', 'Everton FC');
  assert.equal(prediction.fouls.distribution, 'poisson');
  const lambda = prediction.fouls.lambda;
  for (const entry of prediction.fouls.lines) {
    assert.ok(Math.abs(entry.over - poissonOver(lambda, entry.line)) < 1e-9);
  }
});
