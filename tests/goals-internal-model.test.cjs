const test = require('node:test');
const assert = require('node:assert/strict');
const { poissonPmf, deriveMarkets, predict, resolveLeague, resolveTeam, predictLambda, norm, uniqueFixtures } = require('../goals-internal-model.js');

// Minimal synthetic model that mirrors the schemaVersion 2 goals export format.
const sampleModel = {
  schemaVersion: 2,
  trainingThrough: '2026-10-29',
  marketLines: { totalGoals: [1.5, 2.5, 3.5], teamGoals: [0.5, 1.5] },
  runtimePolicy: { minimumTeamMatches: 5, maxGoals: 10 },
  leagueNames: { E0: 'England - Premier League' },
  leagueAliases: { 'premier league': 'E0', 'england premier league': 'E0' },
  leagueStats: { E0: { league_corners_avg: 10.2, league_cards_avg: 3.9, league_goals_avg: 2.7, league_fouls_avg: 21, league_matches_before: 1000 } },
  teams: {
    E0: {
      newcastle: { name: 'Newcastle', matchesBefore: 100, stats: { goal_team_for_avg5: 1.8, goal_team_against_avg5: 1.0 } },
      everton: { name: 'Everton', matchesBefore: 100, stats: { goal_team_for_avg5: 0.9, goal_team_against_avg5: 1.6 } },
      newcomer: { name: 'Newcomer', matchesBefore: 2, stats: {} },
    },
  },
  models: {
    homeGoals: {
      type: 'poisson',
      features: ['home_goal_team_for_avg5', 'away_goal_team_against_avg5', 'league_goals_avg', 'home_advantage', 'home_shots_team_for_avg5', 'home_shots_team_for_avg5__missing', 'league__E0'],
      imputerMedian: [1.4, 1.3, 2.7, 1, 12, 0, 0],
      scalerMean: [1.4, 1.3, 2.7, 1, 12, 0.1, 0.5],
      scalerScale: [0.5, 0.5, 0.4, 1, 3, 0.3, 0.5],
      coefficients: [0.05, -0.04, 0.02, 0, 0.01, 0, 0],
      intercept: 0.15,
    },
    awayGoals: {
      type: 'poisson',
      features: ['away_goal_team_for_avg5', 'home_goal_team_against_avg5', 'league_goals_avg', 'home_advantage', 'away_shots_team_for_avg5', 'away_shots_team_for_avg5__missing', 'league__E0'],
      imputerMedian: [1.2, 1.4, 2.7, 1, 11, 0, 0],
      scalerMean: [1.2, 1.4, 2.7, 1, 11, 0.1, 0.5],
      scalerScale: [0.5, 0.5, 0.4, 1, 3, 0.3, 0.5],
      coefficients: [0.05, -0.04, 0.02, 0, 0.01, 0, 0],
      intercept: 0.0,
    },
  },
};

test('poissonPmf matches a hand-computed value and sums to 1', () => {
  // P(X = 3) for lambda = 2: e^-2 * 2^3 / 3! = 0.1804470...
  assert.ok(Math.abs(poissonPmf(3, 2) - 0.1804470) < 1e-6);
  let total = 0;
  for (let k = 0; k <= 30; k++) total += poissonPmf(k, 1.35);
  assert.ok(Math.abs(total - 1) < 1e-9);
});

test('deriveMarkets: outcomes are consistent and cover the matrix', () => {
  const m = deriveMarkets(1.6, 1.1, [1.5, 2.5, 3.5], [0.5, 1.5], 10);
  const o = m.outcomes;
  assert.ok(Math.abs(o.p1 + o.x + o.p2 - 1) < 1e-9);
  // Double chance identities must hold exactly.
  assert.ok(Math.abs(o.dc1x - (o.p1 + o.x)) < 1e-12);
  assert.ok(Math.abs(o.dc12 - (o.p1 + o.p2)) < 1e-12);
  assert.ok(Math.abs(o.dcx2 - (o.x + o.p2)) < 1e-12);
  // Over probabilities decrease with the line.
  const overs = m.totalGoals.map(t => t.over);
  assert.ok(overs[0] > overs[1] && overs[1] > overs[2]);
  // Half lines cannot push: under is exactly the complement of over.
  for (const t of m.totalGoals) assert.ok(Math.abs(t.under - (1 - t.over)) < 1e-12);
  for (const side of ['home', 'away']) {
    for (const t of m.teamGoals[side]) assert.ok(Math.abs(t.under - (1 - t.over)) < 1e-12);
  }
  // Team total over 0.5 is just 1 - P(0 goals).
  assert.ok(Math.abs(m.teamGoals.home[0].over - (1 - poissonPmf(0, 1.6))) < 1e-12);
  assert.ok(Math.abs(m.teamGoals.away[0].over - (1 - poissonPmf(0, 1.1))) < 1e-12);
});

test('deriveMarkets: higher lambda shifts the outcome towards that side', () => {
  const homeHeavy = deriveMarkets(2.2, 0.8, [2.5], [0.5], 10);
  const awayHeavy = deriveMarkets(0.8, 2.2, [2.5], [0.5], 10);
  assert.ok(homeHeavy.outcomes.p1 > homeHeavy.outcomes.p2);
  assert.ok(awayHeavy.outcomes.p2 > awayHeavy.outcomes.p1);
  // Symmetry: swapping lambdas swaps p1 and p2.
  assert.ok(Math.abs(homeHeavy.outcomes.p1 - awayHeavy.outcomes.p2) < 1e-9);
});

test('predictLambda reproduces the linear predictor with scaling and imputation', () => {
  const spec = sampleModel.models.homeGoals;
  const ctx = { home_goal_team_for_avg5: 1.8, away_goal_team_against_avg5: 1.6, league_goals_avg: 2.7, home_advantage: 1 };
  // home_shots_team_for_avg5 missing -> median 12, indicator 1.
  const expected = Math.exp(0.15
    + 0.05 * ((1.8 - 1.4) / 0.5) - 0.04 * ((1.6 - 1.3) / 0.5) + 0.02 * ((2.7 - 2.7) / 0.4)
    + 0 * ((1 - 1) / 1) + 0.01 * ((12 - 12) / 3) + 0 * ((1 - 0.1) / 0.3) + 0 * ((1 - 0.5) / 0.5));
  assert.ok(Math.abs(predictLambda(spec, ctx, 'E0') - expected) < 1e-9);
});

test('predict scores a fully matched fixture with all requested markets', () => {
  const result = predict(sampleModel, 'Premier League', 'Newcastle United', 'Everton FC');
  assert.ok(result);
  assert.equal(result.homeModel, 'Newcastle');
  assert.equal(result.awayModel, 'Everton');
  assert.ok(result.homeLambda > result.awayLambda); // stronger attack vs weaker defence
  const o = result.outcomes;
  assert.ok(Math.abs(o.p1 + o.x + o.p2 - 1) < 1e-9);
  assert.deepEqual(result.totalGoals.map(t => t.line), [1.5, 2.5, 3.5]);
  assert.deepEqual(result.teamGoals.home.map(t => t.line), [0.5, 1.5]);
  assert.deepEqual(result.teamGoals.away.map(t => t.line), [0.5, 1.5]);
  for (const t of result.totalGoals) {
    assert.ok(t.over > 0 && t.over < 1);
    assert.ok(Math.abs(t.over + t.under - 1) < 1e-12);
  }
  for (const side of ['home', 'away']) {
    for (const t of result.teamGoals[side]) {
      assert.ok(Math.abs(t.over + t.under - 1) < 1e-12);
    }
  }
});

test('never invents probabilities for unknown leagues, teams or thin history', () => {
  assert.equal(predict(sampleModel, 'MLS', 'Newcastle United', 'Everton'), null);
  assert.equal(predict(sampleModel, 'Premier League', 'Newcastle United', 'Inter Miami'), null);
  assert.equal(predict(sampleModel, 'Premier League', 'Newcastle United', 'Newcomer'), null);
  assert.equal(predict(null, 'Premier League', 'Newcastle', 'Everton'), null);
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

test('norm strips club prefixes like the export normalizer', () => {
  assert.equal(norm('FC Koln'), 'koln');
  assert.equal(norm('RC Deportivo La Coruña'), 'rc deportivo la coruna');
});
