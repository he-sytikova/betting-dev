const test = require('node:test');
const assert = require('node:assert/strict');

const api = require('../euro-stats-internal-model.js');

function statsModel() {
  return {
    schemaVersion: 1,
    competitions: [
      { code: 'CL', name: 'UEFA Champions League', names: ['UEFA Champions League', 'Champions League'] },
      { code: 'EL', name: 'UEFA Europa League', names: ['UEFA Europa League', 'Europa League'] },
    ],
    compAverages: { CL: { corners: 10.2, cards: 4.1 }, EL: { corners: 9.8, cards: 4.4 } },
    teamAliases: { 'real madrid': 'real-madrid', psv: 'psv' },
    teams: {
      'real-madrid': {
        name: 'Real Madrid',
        euro: {
          euroElo: 1900, euroGf: 2.1, euroGa: 0.9,
          euro_corners_for_avg5: 6.2, euro_corners_against_avg5: 3.4,
          euro_corners_for_avg10: 6.0, euro_corners_against_avg10: 3.5,
          euro_corners_games: 8,
          euro_cards_for_avg5: 1.8, euro_cards_against_avg5: 2.2,
          euro_cards_for_avg10: 1.9, euro_cards_against_avg10: 2.1,
          euro_cards_games: 12,
        },
        domestic: { corners_for_avg5: 6.5, cards_for_avg5: 1.7 },
      },
      psv: {
        name: 'PSV',
        euro: {
          euroElo: 1700, euroGf: 1.4, euroGa: 1.3,
          euro_corners_for_avg5: 4.4, euro_corners_against_avg5: 5.1,
          euro_corners_for_avg10: 4.5, euro_corners_against_avg10: 5.0,
          euro_corners_games: 6,
          euro_cards_for_avg5: 2.4, euro_cards_against_avg5: 1.6,
          euro_cards_for_avg10: 2.3, euro_cards_against_avg10: 1.7,
          euro_cards_games: 12,
        },
      },
    },
    models: {
      corners: {
        type: 'poisson',
        features: [], imputerMedian: [], scalerMean: [], scalerScale: [],
        coefficients: [], intercept: Math.log(11), lines: [8.5, 9.5, 10.5],
      },
      yellowCards: {
        type: 'nb2', nbAlpha: 0.08,
        features: [], imputerMedian: [], scalerMean: [], scalerScale: [],
        coefficients: [], intercept: Math.log(4.5), lines: [2.5, 3.5, 4.5],
      },
    },
    trainingThrough: '2026-11-20',
  };
}

test('norm mirrors the euro model normalization', () => {
  assert.equal(api.norm('FK Bodø/Glimt'), 'fk bod glimt');
  assert.equal(api.norm('  Paris   Saint-Germain FC '), 'paris saint germain fc');
});

test('competition gate accepts model tournaments and rejects lookalikes', () => {
  const model = statsModel();
  assert.equal(api.resolveCompetition(model, 'Champions League').code, 'CL');
  assert.equal(api.resolveCompetition(model, 'UEFA Europa League').code, 'EL');
  assert.equal(api.resolveCompetition(model, 'AFC Champions League'), null);
  assert.equal(api.resolveCompetition(model, 'UEFA Youth League'), null);
  assert.equal(api.resolveCompetition(model, 'Conference League'), null); // ECL нет в модели
});

test('team resolution goes through aliases only', () => {
  const model = statsModel();
  assert.equal(api.resolveTeam(model, 'Real Madrid').key, 'real-madrid');
  assert.equal(api.resolveTeam(model, 'Unknown FC'), null);
});

test('predict returns club-shaped markets with over/under summing to 1', () => {
  const model = statsModel();
  const p = api.predict(model, 'UEFA Champions League', 'Real Madrid', 'PSV');
  assert.ok(p);
  assert.equal(p.leagueLabel, 'UEFA Champions League');
  // Пустой набор признаков -> lambda = exp(intercept).
  assert.ok(Math.abs(p.corners.lambda - 11) < 1e-9);
  assert.equal(p.corners.distribution, 'poisson');
  assert.equal(p.yellowCards.distribution, 'nb2');
  for (const market of ['corners', 'yellowCards']) {
    for (const line of p[market].lines) {
      assert.ok(line.over >= 0 && line.over <= 1);
      assert.ok(Math.abs(line.over + line.under - 1) < 1e-12);
    }
  }
  // lambda угловых 11: ТБ 8.5 вероятнее ТМ.
  const over85 = p.corners.lines.find(l => l.line === 8.5);
  assert.ok(over85.over > 0.5);
});

test('predict returns null for unknown league or team instead of inventing probabilities', () => {
  const model = statsModel();
  assert.equal(api.predict(model, 'AFC Champions League', 'Real Madrid', 'PSV'), null);
  assert.equal(api.predict(model, 'Champions League', 'Real Madrid', 'Nope FC'), null);
  assert.equal(api.predict(null, 'Champions League', 'Real Madrid', 'PSV'), null);
  assert.equal(api.predict({ schemaVersion: 2 }, 'Champions League', 'Real Madrid', 'PSV'), null);
});

test('canonicalFixtureKey collapses name variants to model identity', () => {
  const model = statsModel();
  assert.equal(
    api.canonicalFixtureKey(model, 'UEFA Champions League', 'Real Madrid', 'PSV'),
    'eurostats|CL|real-madrid|psv');
  assert.equal(
    api.canonicalFixtureKey(model, 'Champions League', 'Real Madrid', 'PSV'),
    'eurostats|CL|real-madrid|psv');
  assert.equal(
    api.canonicalFixtureKey(model, 'England. Premier League', 'Newcastle', 'Everton'),
    'raw|newcastle|everton');
});

test('context maps euro and domestic stat features with nulls for missing domestic', () => {
  const model = statsModel();
  const comp = api.resolveCompetition(model, 'Europa League');
  const home = api.resolveTeam(model, 'Real Madrid');
  const away = api.resolveTeam(model, 'PSV');
  const ctx = api.buildContext(model, comp, home, away);
  assert.equal(ctx.comp_is_el, 1);
  assert.equal(ctx.comp_is_ecl, 0);
  assert.equal(ctx.comp_corners_avg, 9.8);
  assert.equal(ctx.comp_cards_avg, 4.4);
  assert.equal(ctx.home_euro_corners_for_avg5, 6.2);
  assert.equal(ctx.away_euro_cards_games, 12);
  assert.equal(ctx.home_dom_corners_for_avg5, 6.5);
  assert.equal(ctx.home_dom_missing, 0);
  // у PSV нет домашнего снимка — null и индикатор пропуска
  assert.equal(ctx.away_dom_corners_for_avg5, null);
  assert.equal(ctx.away_dom_missing, 1);
});

test('calibration knots interpolate and clamp', () => {
  const knots = { x: [0.2, 0.5, 0.8], y: [0.1, 0.4, 0.9] };
  assert.equal(api.calibrate(0.1, knots), 0.1);
  assert.equal(api.calibrate(0.95, knots), 0.9);
  assert.ok(Math.abs(api.calibrate(0.5, knots) - 0.4) < 1e-12);
  assert.ok(Math.abs(api.calibrate(0.35, knots) - 0.25) < 1e-12);
});

test('nbOver matches scipy nbinom tails (parity with the trainer)', () => {
  // Эталоны: scipy nbinom.cdf при r=1/alpha, p=r/(r+mean).
  const lambda = 4.5, alpha = 0.08;
  assert.ok(Math.abs(api.nbOver(lambda, 2.5, alpha) - 0.78109619) < 1e-7);
  assert.ok(Math.abs(api.nbOver(lambda, 3.5, alpha) - 0.61909621) < 1e-7);
  assert.ok(Math.abs(api.nbOver(lambda, 4.5, alpha) - 0.45292712) < 1e-7);
  assert.ok(Math.abs(api.poissonOver(lambda, 2.5) - 0.82642193) < 1e-7);
});
