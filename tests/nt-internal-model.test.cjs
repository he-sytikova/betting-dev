const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const nt = require('../nt-internal-model.js');
const modelPath = path.join(__dirname, '..', 'data', 'nt_model.json');
// Артефакт появляется после первого Retrain с NT-пайплайном; до этого
// момента тесты модели пропускаются (как Python-тесты без CSV).
const modelExists = fs.existsSync(modelPath);
const model = modelExists ? JSON.parse(fs.readFileSync(modelPath, 'utf8')) : null;

test('norm mirrors the Python exporter normalization', () => {
  assert.equal(nt.norm('Côte d’Ivoire'), 'cote d ivoire');
  assert.equal(nt.norm('  Türkiye '), 'turkiye');
  assert.equal(nt.norm('São Tomé and Príncipe'), 'sao tome and principe');
});

test('competition gate accepts UEFA Nations League names only', { skip: !modelExists }, () => {
  assert.ok(Array.isArray(model.competitions) && model.competitions.length > 0);
  for (const comp of model.competitions) {
    for (const name of comp.names) {
      const resolved = nt.resolveCompetition(model, name);
      assert.ok(resolved, `${name} must resolve`);
      assert.equal(resolved.code, comp.code);
    }
  }
});

test('competition gate rejects lookalike and out-of-scope tournaments', { skip: !modelExists }, () => {
  // ЛН КОНКАКАФ и женская ЛН — другие турниры, вероятности не выдумываем.
  assert.equal(nt.resolveCompetition(model, 'CONCACAF Nations League'), null);
  assert.equal(nt.resolveCompetition(model, 'UEFA Women’s Nations League'), null);
  assert.equal(nt.resolveCompetition(model, 'UEFA Champions League'), null);
  assert.equal(nt.resolveCompetition(model, 'FIFA World Cup'), null);
  assert.equal(nt.resolveCompetition(model, 'Friendly'), null);
  assert.equal(nt.resolveCompetition(model, ''), null);
});

test('feed name variants resolve to the same team', { skip: !modelExists }, () => {
  const variants = ['Türkiye', 'Turkey'];
  const keys = variants.map(v => (nt.resolveTeam(model, v) || {}).key);
  assert.ok(keys[0], 'Türkiye must resolve');
  assert.equal(new Set(keys).size, 1, `variants disagree: ${keys}`);
});

test('predict returns normalized 1X2 for a well-known fixture', { skip: !modelExists }, () => {
  const pred = nt.predict(model, 'UEFA Nations League', 'Spain', 'France');
  assert.ok(pred);
  assert.equal(pred.competition.code, 'UNL');
  const { p1, x, p2 } = pred.markets;
  const sum = p1 + x + p2;
  assert.ok(Math.abs(sum - 1) < 1e-6, `1X2 sum ${sum}`);
  assert.ok(p1 > p2, 'home favourite expected');
  assert.ok(Math.abs(pred.markets.dc1x - (p1 + x)) < 1e-9);
  const t25 = pred.markets.totals['2.5'] || pred.markets.totals[2.5];
  assert.ok(Math.abs(t25.over + t25.under - 1) < 1e-6);
});

test('unknown team or wrong league yields null, never invented probabilities', { skip: !modelExists }, () => {
  assert.equal(nt.predict(model, 'UEFA Nations League', 'Spain', 'Atlantis XI'), null);
  assert.equal(nt.predict(model, 'CONCACAF Nations League', 'Spain', 'France'), null);
  assert.equal(nt.predict(model, 'UEFA Champions League', 'Spain', 'France'), null);
});

test('scoring context is fixed: competitive non-finals, non-neutral', { skip: !modelExists }, () => {
  const home = nt.resolveTeam(model, 'Spain');
  const away = nt.resolveTeam(model, 'France');
  assert.ok(home && away);
  const base = nt.baseFeatureVector(model, home, away);
  assert.equal(base.is_neutral, 0);
  assert.equal(base.ctx_is_friendly, 0);
  assert.equal(base.ctx_is_finals, 0);
});

test('hand-computed lambda matches predictLambda', { skip: !modelExists }, () => {
  const home = nt.resolveTeam(model, 'Spain');
  const away = nt.resolveTeam(model, 'France');
  const base = nt.baseFeatureVector(model, home, away);
  const vector = nt.transform(model, base);
  const spec = model.models.home;
  let z = spec.intercept;
  for (let i = 0; i < spec.coefficients.length; i++) z += spec.coefficients[i] * vector[i];
  const expected = Math.max(Math.exp(z), 0.01);
  assert.ok(Math.abs(nt.predictLambda(model, vector, 'home') - expected) < 1e-12);
});

test('dcTau with deployed rho keeps low-score cells positive', { skip: !modelExists }, () => {
  for (const [i, j] of [[0, 0], [0, 1], [1, 0], [1, 1]]) {
    const t = nt.dcTau(i, j, 1.4, 1.1, model.dcRho || 0);
    assert.ok(Number.isFinite(t) && t > 0);
  }
});

test('exported transform dimensions line up', { skip: !modelExists }, () => {
  assert.equal(model.features.length, model.imputerMedian.length);
  assert.equal(model.features.length, model.scalerMean.length);
  assert.equal(model.features.length, model.scalerScale.length);
  assert.equal(model.features.length, model.models.home.coefficients.length);
  assert.equal(model.features.length, model.models.away.coefficients.length);
});

test('every exported team has the full feature set', { skip: !modelExists }, () => {
  for (const [key, team] of Object.entries(model.teams)) {
    for (const f of ['ntElo', 'gfDecay', 'gaDecay', 'gfAvg5', 'gaAvg5']) {
      assert.ok(Number.isFinite(team[f]), `${key}.${f} must be finite`);
    }
    assert.ok(team.name && typeof team.name === 'string');
  }
});
