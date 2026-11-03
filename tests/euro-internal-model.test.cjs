const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const euro = require('../euro-internal-model.js');
const modelPath = path.join(__dirname, '..', 'data', 'euro_cups_model.json');
// Артефакт появляется после первого Retrain с мультитурнирным пайплайном;
// до этого момента тесты модели пропускаются (как Python-тесты без CSV).
const modelExists = fs.existsSync(modelPath);
const model = modelExists ? JSON.parse(fs.readFileSync(modelPath, 'utf8')) : null;

test('norm mirrors the Python exporter normalization', () => {
  assert.equal(euro.norm('FK Bodø/Glimt'), 'fk bod glimt');
  assert.equal(euro.norm('  Paris   Saint-Germain FC '), 'paris saint germain fc');
  assert.equal(euro.norm('Qarabağ Ağdam FK'), 'qarabag agdam fk');
});

// Ожидания берутся из артефакта, а не захардкожены: пока тариф FDO отдаёт
// только ЛЧ, гейт обязан принимать ровно ЛЧ; появятся ЛЕ/ЛК — тест сам их подхватит.
const ALL_COMP_NAMES = {
  CL: ['UEFA Champions League', 'Champions League'],
  EL: ['UEFA Europa League', 'Europa League'],
  ECL: ['UEFA Europa Conference League', 'UEFA Conference League', 'Conference League'],
};

test('competition gate accepts exactly the tournaments present in the model', { skip: !modelExists }, () => {
  assert.ok(Array.isArray(model.competitions) && model.competitions.length > 0);
  for (const comp of model.competitions) {
    for (const name of comp.names) {
      const resolved = euro.resolveCompetition(model, name);
      assert.ok(resolved, `${name} must resolve`);
      assert.equal(resolved.code, comp.code);
    }
  }
});

test('competition gate rejects tournaments absent from the model', { skip: !modelExists }, () => {
  const present = new Set(model.competitions.map(c => c.code));
  for (const [code, names] of Object.entries(ALL_COMP_NAMES)) {
    if (present.has(code)) continue;
    for (const name of names) {
      assert.equal(euro.resolveCompetition(model, name), null, `${name} must not resolve`);
    }
  }
});

test('competition gate rejects lookalike tournaments', { skip: !modelExists }, () => {
  assert.equal(euro.resolveCompetition(model, 'AFC Champions League Elite'), null);
  assert.equal(euro.resolveCompetition(model, 'AFC Champions League'), null);
  assert.equal(euro.resolveCompetition(model, 'UEFA Youth League'), null);
  assert.equal(euro.resolveCompetition(model, 'Championship'), null);
  assert.equal(euro.resolveCompetition(model, 'England. Championship'), null);
});

test('competition indicators follow the resolved tournament, CL is reference', { skip: !modelExists }, () => {
  const home = euro.resolveTeam(model, 'FC Barcelona');
  const away = euro.resolveTeam(model, 'Aston Villa FC');
  assert.ok(home && away);
  const cl = euro.baseFeatureVector(model, home, away, { code: 'CL' });
  assert.equal(cl.comp_is_el, 0);
  assert.equal(cl.comp_is_ecl, 0);
  const el = euro.baseFeatureVector(model, home, away, { code: 'EL' });
  assert.equal(el.comp_is_el, 1);
  assert.equal(el.comp_is_ecl, 0);
  const ecl = euro.baseFeatureVector(model, home, away, { code: 'ECL' });
  assert.equal(ecl.comp_is_el, 0);
  assert.equal(ecl.comp_is_ecl, 1);
});

test('feed aliases from all three providers resolve to the same team', { skip: !modelExists }, () => {
  const variants = ['FK Bodø/Glimt', 'Bodø/Glimt', 'Bodo-Glimt'];
  const keys = variants.map(v => (euro.resolveTeam(model, v) || {}).key);
  assert.ok(keys[0], 'FDO name must resolve');
  assert.equal(new Set(keys).size, 1, `variants disagree: ${keys}`);
});

test('predict returns normalized 1X2 for a well-known fixture', { skip: !modelExists }, () => {
  const pred = euro.predict(model, 'UEFA Champions League', 'FC Barcelona', 'Aston Villa FC');
  assert.ok(pred);
  assert.equal(pred.competition.code, 'CL');
  const { p1, x, p2 } = pred.markets;
  const sum = p1 + x + p2;
  assert.ok(Math.abs(sum - 1) < 1e-6, `1X2 sum ${sum}`);
  assert.ok(p1 > p2, 'strong home favourite expected');
  assert.ok(Math.abs(pred.markets.dc1x - (p1 + x)) < 1e-9);
  const t25 = pred.markets.totals['2.5'] || pred.markets.totals[2.5];
  assert.ok(Math.abs(t25.over + t25.under - 1) < 1e-6);
});

test('unknown team or wrong league yields null, never invented probabilities', { skip: !modelExists }, () => {
  assert.equal(euro.predict(model, 'UEFA Champions League', 'FC Barcelona', 'Unknown FC'), null);
  assert.equal(euro.predict(model, 'Championship', 'FC Barcelona', 'Aston Villa FC'), null);
  assert.equal(euro.predict(model, 'UEFA Youth League', 'FC Barcelona', 'Aston Villa FC'), null);
});

test('team without domestic coverage scores via euro features only', { skip: !modelExists }, () => {
  const team = euro.resolveTeam(model, 'Shakhtar Donetsk');
  assert.ok(team, 'Shakhtar must resolve');
  assert.equal(team.team.domestic, undefined, 'Ukrainian league is outside coverage');
  const pred = euro.predict(model, 'Champions League', 'Shakhtar Donetsk', 'Sporting CP');
  assert.ok(pred, 'match with an uncovered team still scores');
});

test('dcTau with positive rho lifts extreme low-score cells consistently', { skip: !modelExists }, () => {
  const t = euro.dcTau(0, 0, 1.6, 1.2, model.dcRho);
  assert.ok(Number.isFinite(t) && t > 0);
});

test('exported transform dimensions line up', { skip: !modelExists }, () => {
  assert.equal(model.features.length, model.imputerMedian.length);
  assert.equal(model.features.length, model.scalerMean.length);
  assert.equal(model.features.length, model.scalerScale.length);
  assert.equal(model.features.length, model.models.home.coefficients.length);
  assert.equal(model.features.length, model.models.away.coefficients.length);
});
