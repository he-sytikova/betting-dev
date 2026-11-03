const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const euro = require('../euro-internal-model.js');
const model = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'euro_cl_model.json'), 'utf8'));

test('norm mirrors the Python exporter normalization', () => {
  assert.equal(euro.norm('FK Bodø/Glimt'), 'fk bod glimt');
  assert.equal(euro.norm('  Paris   Saint-Germain FC '), 'paris saint germain fc');
  assert.equal(euro.norm('Qarabağ Ağdam FK'), 'qarabag agdam fk');
});

test('league gate accepts only Champions League spellings', () => {
  assert.ok(euro.isChampionsLeague('UEFA Champions League'));
  assert.ok(euro.isChampionsLeague('Champions League'));
  assert.ok(!euro.isChampionsLeague('AFC Champions League Elite'));
  assert.ok(!euro.isChampionsLeague('Championship'));
  assert.ok(!euro.isChampionsLeague('England. Championship'));
});

test('feed aliases from all three providers resolve to the same team', () => {
  const variants = ['FK Bodø/Glimt', 'Bodø/Glimt', 'Bodo-Glimt'];
  const keys = variants.map(v => (euro.resolveTeam(model, v) || {}).key);
  assert.ok(keys[0], 'FDO name must resolve');
  assert.equal(new Set(keys).size, 1, `variants disagree: ${keys}`);
});

test('predict returns normalized 1X2 for a well-known fixture', () => {
  const pred = euro.predict(model, 'UEFA Champions League', 'FC Barcelona', 'Aston Villa FC');
  assert.ok(pred);
  const { p1, x, p2 } = pred.markets;
  const sum = p1 + x + p2;
  assert.ok(Math.abs(sum - 1) < 1e-6, `1X2 sum ${sum}`);
  assert.ok(p1 > p2, 'strong home favourite expected');
  assert.ok(Math.abs(pred.markets.dc1x - (p1 + x)) < 1e-9);
  const t25 = pred.markets.totals['2.5'] || pred.markets.totals[2.5];
  assert.ok(Math.abs(t25.over + t25.under - 1) < 1e-6);
});

test('unknown team or wrong league yields null, never invented probabilities', () => {
  assert.equal(euro.predict(model, 'UEFA Champions League', 'FC Barcelona', 'Unknown FC'), null);
  assert.equal(euro.predict(model, 'Championship', 'FC Barcelona', 'Aston Villa FC'), null);
});

test('team without domestic coverage scores via euro features only', () => {
  const team = euro.resolveTeam(model, 'Shakhtar Donetsk');
  assert.ok(team, 'Shakhtar must resolve');
  assert.equal(team.team.domestic, undefined, 'Ukrainian league is outside coverage');
  const pred = euro.predict(model, 'Champions League', 'Shakhtar Donetsk', 'Sporting CP');
  assert.ok(pred, 'match with an uncovered team still scores');
});

test('dcTau with positive rho lifts extreme low-score cells consistently', () => {
  const t = euro.dcTau(0, 0, 1.6, 1.2, model.dcRho);
  assert.ok(Number.isFinite(t) && t > 0);
});

test('exported transform dimensions line up', () => {
  assert.equal(model.features.length, model.imputerMedian.length);
  assert.equal(model.features.length, model.scalerMean.length);
  assert.equal(model.features.length, model.scalerScale.length);
  assert.equal(model.features.length, model.models.home.coefficients.length);
  assert.equal(model.features.length, model.models.away.coefficients.length);
});
