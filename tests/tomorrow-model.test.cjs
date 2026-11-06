'use strict';

/* Tests for tomorrow-model.js: Moscow-day keys and the model-routing logic
 * of the "forecasts for tomorrow" page. Stubs stand in for the real model
 * APIs; the contract under test is that a fixture goes to the FIRST model
 * that can score it and that unscorable fixtures are counted, not shown. */

const test = require('node:test');
const assert = require('node:assert/strict');

const tomorrow = require('../tomorrow-model.js');

test('moscowDayKey: UTC evening rolls into the next Moscow day', () => {
  assert.equal(tomorrow.moscowDayKey(new Date('2026-11-06T22:30:00Z')), '2026-11-07');
  assert.equal(tomorrow.moscowDayKey(new Date('2026-11-06T20:59:59Z')), '2026-11-06');
});

test('tomorrowKey adds one Moscow day', () => {
  assert.equal(tomorrow.tomorrowKey(new Date('2026-11-06T10:00:00Z')), '2026-11-07');
  // 23:30 MSK = 20:30 UTC: завтра всё ещё 7-е
  assert.equal(tomorrow.tomorrowKey(new Date('2026-11-06T20:30:00Z')), '2026-11-07');
  // 00:30 MSK 7-го = 21:30 UTC 6-го: завтра уже 8-е
  assert.equal(tomorrow.tomorrowKey(new Date('2026-11-06T21:30:00Z')), '2026-11-08');
});

const fixture = { eventId: '1', time: '2026-11-07T15:00:00+00:00', league: 'X', home: 'A', away: 'B' };

function apisWith({ club = null, euro = null, nt = null } = {}) {
  return {
    goals: { predict: () => club },
    euro: { predict: () => euro },
    nt: { predict: () => nt },
  };
}
const models = { goals: {}, euro: {}, nt: {} };

test('classifyFixture: club model wins when it can score', () => {
  const hit = tomorrow.classifyFixture(apisWith({ club: { ok: 1 }, euro: { ok: 2 } }), models, fixture);
  assert.equal(hit.kind, 'club');
  assert.deepEqual(hit.prediction, { ok: 1 });
});

test('classifyFixture: falls through to euro, then to national teams', () => {
  assert.equal(tomorrow.classifyFixture(apisWith({ euro: { ok: 2 } }), models, fixture).kind, 'euro');
  assert.equal(tomorrow.classifyFixture(apisWith({ nt: { ok: 3 } }), models, fixture).kind, 'nt');
  assert.equal(tomorrow.classifyFixture(apisWith(), models, fixture), null);
});

test('classifyFixture: absent model artifacts are skipped safely', () => {
  const noEuroModels = { goals: {}, euro: null, nt: {} };
  const hit = tomorrow.classifyFixture(apisWith({ euro: { ok: 2 }, nt: { ok: 3 } }), noEuroModels, fixture);
  assert.equal(hit.kind, 'nt');
});

test('buildPredictions groups by model and counts the unscored honestly', () => {
  const apis = apisWith({ club: { m: 'c' } });
  // Разные матчи: первый оценивает клубная модель, остальные — никто.
  const altApis = {
    goals: { predict: (m, league, home) => (home === 'A' ? { m: 'c' } : null) },
    euro: { predict: (m, league, home) => (home === 'C' ? { m: 'e' } : null) },
    nt: { predict: (m, league, home) => (home === 'E' ? { m: 'n' } : null) },
  };
  const fixtures = [
    { ...fixture, home: 'A' },
    { ...fixture, home: 'C' },
    { ...fixture, home: 'E' },
    { ...fixture, home: 'G' },
  ];
  const { groups, unscored } = tomorrow.buildPredictions(altApis, fixtures, models);
  assert.equal(groups.club.length, 1);
  assert.equal(groups.euro.length, 1);
  assert.equal(groups.nt.length, 1);
  assert.equal(unscored, 1);
  assert.equal(groups.club[0].prediction.m, 'c');
});

test('buildPredictions: empty or missing fixture list', () => {
  const { groups, unscored } = tomorrow.buildPredictions(apisWith(), null, models);
  assert.deepEqual(groups, { club: [], euro: [], nt: [] });
  assert.equal(unscored, 0);
});
