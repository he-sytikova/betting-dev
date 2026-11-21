const test = require('node:test');
const assert = require('node:assert/strict');
const report = require('../financial-report.js');

const DAY = '2027-03-10';
const TIME = `${DAY}T18:00:00.000Z`;

class MemoryStorage {
  constructor() { this.values = new Map(); this.writes = 0; }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); this.writes += 1; }
}

function leg(label = 'П1', overrides = {}) {
  return { home: 'Home', away: 'Away', time: TIME, label, model: 'Голы', eventId: 'e1', odds: 2, ...overrides };
}

function record(overrides = {}) {
  const legs = overrides.legs || [leg()];
  return {
    day: DAY, amount: 100, betType: 'money', confirmedAt: `${DAY}T12:00:00.000Z`,
    coupon: { type: legs.length === 2 ? 'double' : 'single', bookmaker: 'Fonbet', odds: 2, legs },
    ...overrides,
  };
}

function result(overrides = {}) {
  return { event_id: 'e1', home: 'Home', away: 'Away', time: TIME, status: 'finished',
    home_goals: 2, away_goals: 1, total_corners: 11, ...overrides };
}

function index(results = [result()]) {
  return report.buildResultIndex([{ date: DAY, results }]);
}

test('exact event ID settles all 1X2 and double-chance markets', () => {
  const expectations = { 'П1': 'win', 'П2': 'loss', 'Х': 'loss', '1X': 'win', 'X2': 'loss', '12': 'win' };
  for (const [label, status] of Object.entries(expectations)) {
    assert.equal(report.settleLeg(leg(label), index()).status, status, label);
  }
  assert.equal(report.settleLeg(leg('Х'), index([result({ home_goals: 1, away_goals: 1 })])).status, 'win');
  assert.equal(report.settleLeg(leg('X2'), index([result({ home_goals: 1, away_goals: 1 })])).status, 'win');
  assert.equal(report.settleLeg(leg('12'), index([result({ home_goals: 1, away_goals: 1 })])).status, 'loss');
});

test('goals, team totals and integer pushes use final goals only', () => {
  const cases = [
    ['ТБ 2.5', 'win'], ['ТМ 2.5', 'loss'], ['ИТБ1 1.5', 'win'],
    ['ИТМ2 1.5', 'win'], ['ИТБ2 1.5', 'loss'],
  ];
  for (const [label, status] of cases) assert.equal(report.settleLeg(leg(label), index()).status, status, label);
  assert.equal(report.settleLeg(leg('ТБ 3'), index()).status, 'void');
  assert.equal(report.settleLeg(leg('ТМ 3'), index()).status, 'void');
});

test('corners require proven model and final corner statistics', () => {
  assert.equal(report.settleLeg(leg('ТБ 9.5', { model: 'Угловые' }), index()).status, 'win');
  assert.equal(report.settleLeg(leg('ТМ 9.5', { model: 'Угловые' }), index([result({ total_corners: null })])).status, 'pending');
  assert.equal(report.settleLeg(leg('ТБ 9.5', { model: 'Угловые' }), index([result({ total_corners: null })])).reason, 'statistics_missing');
  assert.equal(report.settleLeg(leg('ТБ 2.5', { model: null }), index()).status, 'pending');
  assert.equal(report.settleLeg(leg('ТБ 2.5', { model: null }), index()).reason, 'market_provenance_missing');
});

test('cancelled and abandoned matches are void; postponed and missing results stay pending', () => {
  for (const status of ['cancelled', 'abandoned']) {
    assert.equal(report.settleLeg(leg(), index([result({ status, home_goals: null, away_goals: null })])).status, 'void');
  }
  assert.equal(report.settleLeg(leg(), index([result({ status: 'postponed', home_goals: null, away_goals: null })])).status, 'pending');
  assert.equal(report.settleLeg(leg(), index([])).status, 'pending');
});

test('legacy fixture fallback is exact and ambiguous matches never settle', () => {
  const oldLeg = leg('П1', { eventId: null });
  assert.equal(report.settleLeg(oldLeg, index()).status, 'win');
  assert.equal(report.settleLeg(oldLeg, index([result({ time: `${DAY}T19:00:00Z` })])).status, 'pending');
  assert.equal(report.settleLeg(oldLeg, index([result(), result({ event_id: 'e2' })])).status, 'pending');
  assert.equal(report.settleLeg(leg(), index([result({ event_id: 'another' })])).status, 'pending');
});

test('money single payouts include stake and produce net profit or loss', () => {
  const win = report.settleCoupon(record(), index());
  assert.equal(win.status, 'win');
  assert.equal(win.calculatedPayout, 200);
  assert.equal(win.calculatedNet, 100);
  const loss = report.settleCoupon(record({ legs: [leg('П2')] }), index());
  assert.equal(loss.calculatedPayout, 0);
  assert.equal(loss.calculatedNet, -100);
  const returned = report.settleCoupon(record(), index([result({ status: 'cancelled', home_goals: null, away_goals: null })]));
  assert.equal(returned.calculatedPayout, 100);
  assert.equal(returned.calculatedNet, 0);
});

test('double with one void leg is recalculated using frozen remaining odds', () => {
  const current = record({
    amount: 100,
    legs: [leg('П1', { odds: 1.8 }), leg('П1', { eventId: 'e2', home: 'Second', away: 'Opponent', odds: 2 })],
  });
  current.coupon.odds = 3.6;
  const calculated = report.settleCoupon(current, index([
    result({ status: 'cancelled', home_goals: null, away_goals: null }),
    result({ event_id: 'e2', home: 'Second', away: 'Opponent' }),
  ]));
  assert.equal(calculated.status, 'win');
  assert.equal(calculated.effectiveOdds, 2);
  assert.equal(calculated.calculatedPayout, 200);
  assert.equal(calculated.calculatedNet, 100);
});

test('one losing leg settles a double as loss even when another leg is pending', () => {
  const current = record({ legs: [leg('П2'), leg('П1', { eventId: 'e2', home: 'Second', away: 'Opponent' })] });
  const calculated = report.settleCoupon(current, index());
  assert.equal(calculated.status, 'loss');
  assert.equal(calculated.calculatedPayout, 0);
  assert.equal(calculated.legs[1].status, 'pending');
});

test('freebet payout excludes nominal and never enters money stake', () => {
  const freebet = record({ betType: 'freebet', amount: 100, coupon: { type: 'single', bookmaker: 'Fonbet', odds: 2.5, legs: [leg()] } });
  const calculated = report.settleCoupon(freebet, index());
  assert.equal(calculated.calculatedPayout, 150);
  assert.equal(calculated.calculatedNet, 150);
  const summary = report.buildReport([freebet], [{ results: [result()] }], {}, null).summary;
  assert.equal(summary.money.stake, 0);
  assert.equal(summary.freebet.nominal, 100);
  assert.equal(summary.freebet.calculatedNet, 150);
});

test('calendar periods are Moscow game-day groups with Monday week and full month', () => {
  assert.deepEqual(report.periodRange('day', DAY), { from: DAY, to: DAY });
  assert.deepEqual(report.periodRange('week', DAY), { from: '2027-03-08', to: '2027-03-14' });
  assert.deepEqual(report.periodRange('month', '2027-02-15'), { from: '2027-02-01', to: '2027-02-28' });
  assert.equal(report.periodRange('day', '2027-02-31'), null);
  assert.equal(report.moscowToday(new Date('2027-03-10T22:00:00Z')), '2027-03-11');
});

test('reports filter by original game day and keep pending stake outside finalized ROI', () => {
  const otherDay = record({ day: '2027-03-11', confirmedAt: '2027-03-11T10:00:00Z' });
  const current = report.buildReport([record(), otherDay], [{ results: [result()] }], {}, { from: DAY, to: DAY });
  assert.equal(current.rows.length, 1);
  assert.equal(current.summary.money.calculatedNet, 100);
  assert.equal(current.summary.money.calculatedRoi, 1);
  const pending = report.buildReport([record()], [], {}, null).summary;
  assert.equal(pending.money.pendingStake, 100);
  assert.equal(pending.money.calculatedStake, 0);
  assert.equal(pending.money.calculatedRoi, null);
});

test('owner reconciliation is keyed by stable coupon identity and changes only confirmed finance', () => {
  const current = record();
  const storage = new MemoryStorage();
  const store = report.createReconciliationStore(storage, { now: () => new Date(`${DAY}T23:00:00Z`) });
  const saved = store.save(current, 'loss', '0');
  assert.equal(saved.ok, true);
  assert.equal(storage.writes, 1);
  const calculated = report.buildReport([current], [{ results: [result()] }], store.all(), null);
  assert.equal(calculated.rows[0].automatic.status, 'win');
  assert.equal(calculated.rows[0].confirmed.status, 'loss');
  assert.equal(calculated.summary.money.calculatedNet, 100);
  assert.equal(calculated.summary.money.confirmedNet, -100);
  assert.equal(calculated.summary.money.confirmedRoi, -1);
  assert.deepEqual(store.get(current), saved.record);
});

test('payout validation accepts zero and rejects ambiguous or negative values', () => {
  assert.equal(report.parsePayout('0'), 0);
  assert.equal(report.parsePayout(' 1 250,50 '), 1250.5);
  for (const value of ['', '-1', '+1', '1e3', 'abc', '1,234', null]) assert.equal(report.parsePayout(value), null);
  const store = report.createReconciliationStore(new MemoryStorage());
  assert.equal(store.save(record(), 'other', '100').reason, 'invalid_status');
  assert.equal(store.save(record(), 'win', '-1').reason, 'invalid_payout');
});
