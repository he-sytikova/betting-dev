const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const confirmation = require('../coupon-confirmation.js');
const board = require('../daily-board.js');

const DAY = '2099-01-01';
const FIXED_NOW = new Date('2099-01-01T12:00:00.000Z');

class MemoryStorage {
  constructor() {
    this.values = new Map();
    this.writes = 0;
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.writes += 1;
    this.values.set(key, String(value));
  }
}

function coupon(overrides = {}) {
  const normalized = board.normalizeCoupon({
    coupon_id: 'single-1',
    home: 'Newcastle',
    away: 'Everton',
    league: 'Premier League',
    time: `${DAY}T18:00:00Z`,
    label: '1X',
    probability: 0.8,
    odds: 1.4,
    ev: 1.12,
    bookmaker: 'Fonbet',
    ...overrides,
  }, 0);
  assert.ok(normalized);
  return normalized;
}

function doubleCoupon(overrides = {}) {
  return coupon({
    coupon_id: 'double-1',
    session_id: 'evening',
    legs: [
      {
        home: 'Newcastle', away: 'Everton', league: 'Premier League',
        time: `${DAY}T18:00:00Z`, label: '1X', probability: 0.8, odds: 1.4, ev: 1.12,
      },
      {
        home: 'Spain', away: 'France', league: 'International',
        time: `${DAY}T19:00:00Z`, label: 'ТМ 3.5', probability: 0.85, odds: 1.5, ev: 1.275,
      },
    ],
    odds: 2.1,
    probability: 0.68,
    ev: 1.428,
    bookmaker: 'Fonbet',
    ...overrides,
  });
}

test('stake amount accepts ordinary decimal input and rejects unsafe or ambiguous values', () => {
  assert.equal(confirmation.parseAmount('100'), 100);
  assert.equal(confirmation.parseAmount(' 1 250,50 '), 1250.5);
  assert.equal(confirmation.parseAmount('.25'), 0.25);
  assert.equal(confirmation.parseAmount('0,01'), 0.01);
  for (const value of ['', ' ', '0', '0,00', '-1', '+1', '1e3', 'Infinity', 'NaN', 'abc', '1,234', '1.2.3', '1,2,3']) {
    assert.equal(confirmation.parseAmount(value), null, value);
  }
});

test('coupon identity ignores prices and bookmaker but preserves day and exact composition', () => {
  const original = coupon();
  assert.equal(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ coupon_id: 'another-id' })));
  assert.notEqual(confirmation.confirmationKey(DAY, original), confirmation.confirmationKey('2099-01-02', original));
  assert.equal(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ odds: 1.41 })));
  assert.equal(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ bookmaker: 'Marathonbet' })));
  assert.equal(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ probability: 0.81, ev: 1.2 })));
  assert.notEqual(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ time: `${DAY}T19:00:00Z` })));
  assert.notEqual(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ label: '12' })));
  assert.equal(confirmation.confirmationKey('not-a-day', original), null);
});

test('double identity does not depend on leg order and canonicalizes equivalent start times', () => {
  const original = doubleCoupon();
  const reversed = doubleCoupon({ legs: [...original.legs].reverse() });
  assert.equal(confirmation.couponIdentity(original), confirmation.couponIdentity(reversed));
  const equivalentTime = coupon({ time: '2099-01-01T21:00:00+03:00' });
  assert.equal(confirmation.couponIdentity(coupon()), confirmation.couponIdentity(equivalentTime));
});

test('a new store has no placements and performs no automatic writes', () => {
  const storage = new MemoryStorage();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  assert.equal(store.isAvailable(), true);
  assert.equal(store.get(DAY, coupon()), null);
  assert.deepEqual(store.list(), []);
  assert.equal(storage.writes, 0);
  assert.equal(storage.getItem(confirmation.STORAGE_KEY), null);
});

test('manual money confirmation persists the immutable terms and restores after a reload', () => {
  const storage = new MemoryStorage();
  const current = doubleCoupon();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const result = store.confirm(DAY, current, '1 000,50', 'money');
  assert.equal(result.ok, true);
  assert.deepEqual(result.record, {
    day: DAY,
    amount: 1000.5,
    betType: 'money',
    confirmedAt: FIXED_NOW.toISOString(),
    coupon: confirmation.snapshotCoupon(current),
  });
  assert.equal(storage.writes, 1);
  assert.equal(JSON.parse(storage.getItem(confirmation.STORAGE_KEY)).version, 2);

  const restoredStore = confirmation.createStore(storage);
  assert.deepEqual(restoredStore.get(DAY, current), result.record);
  assert.deepEqual(restoredStore.list(), [result.record]);
  assert.equal(restoredStore.get('2099-01-02', current), null);
});

test('missing model metrics stay null in the snapshot instead of becoming zero', () => {
  const current = coupon();
  current.probability = null;
  current.ev = '';
  current.legs[0].probability = undefined;
  current.legs[0].ev = null;
  const snapshot = confirmation.snapshotCoupon(current);
  assert.equal(snapshot.probability, null);
  assert.equal(snapshot.ev, null);
  assert.equal(snapshot.legs[0].probability, null);
  assert.equal(snapshot.legs[0].ev, null);
});

test('a logical coupon cannot be confirmed twice; editing changes only amount and bet type', () => {
  const storage = new MemoryStorage();
  let now = FIXED_NOW;
  const store = confirmation.createStore(storage, { now: () => now });
  const current = coupon();
  const created = store.confirm(DAY, current, 100, 'freebet');
  assert.equal(created.ok, true);
  const writes = storage.writes;

  const duplicate = store.confirm(DAY, coupon({ odds: 9.99, bookmaker: 'Another BK' }), 200, 'money');
  assert.equal(duplicate.ok, false);
  assert.equal(duplicate.reason, 'already_confirmed');
  assert.deepEqual(duplicate.record, created.record);
  assert.equal(storage.writes, writes);
  assert.equal(store.list().length, 1);

  now = new Date('2099-01-01T13:00:00.000Z');
  const updated = store.update(DAY, coupon({ odds: 1.55 }), '200', 'money');
  assert.equal(updated.ok, true);
  assert.equal(updated.record.amount, 200);
  assert.equal(updated.record.betType, 'money');
  assert.equal(updated.record.confirmedAt, created.record.confirmedAt);
  assert.equal(updated.record.updatedAt, now.toISOString());
  assert.deepEqual(updated.record.coupon, created.record.coupon);
  assert.equal(store.list().length, 1);
});

test('invalid amount or bet type never creates or overwrites a placement', () => {
  const storage = new MemoryStorage();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const current = coupon();
  assert.deepEqual(store.confirm(DAY, current, '0', 'money'), { ok: false, reason: 'invalid_amount' });
  assert.deepEqual(store.confirm(DAY, current, '100', 'other'), { ok: false, reason: 'invalid_bet_type' });
  assert.equal(storage.writes, 0);
  store.confirm(DAY, current, '500', 'money');
  const writes = storage.writes;
  assert.deepEqual(store.confirm(DAY, current, 'abc', 'money'), { ok: false, reason: 'invalid_amount' });
  assert.deepEqual(store.update(DAY, current, '600', 'other'), { ok: false, reason: 'invalid_bet_type' });
  assert.equal(store.get(DAY, current).amount, 500);
  assert.equal(store.get(DAY, current).betType, 'money');
  assert.equal(storage.writes, writes);
});

test('changing published odds and bookmaker preserves the original confirmation and snapshot', () => {
  const storage = new MemoryStorage();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const original = doubleCoupon();
  const created = store.confirm(DAY, original, '500', 'money');
  const changed = doubleCoupon({
    bookmaker: 'Marathonbet',
    odds: 2.35,
    legs: [
      { ...original.legs[0], odds: 1.45 },
      { ...original.legs[1], odds: 1.62 },
    ],
  });

  assert.deepEqual(store.get(DAY, changed), created.record);
  assert.equal(store.get(DAY, changed).coupon.bookmaker, 'Fonbet');
  assert.equal(store.get(DAY, changed).coupon.odds, 2.1);
  assert.deepEqual(store.get(DAY, changed).coupon.legs.map(leg => leg.odds), [1.4, 1.5]);
  assert.equal(confirmation.placementTermsChanged(changed, created.record.coupon), true);
  assert.equal(confirmation.placementTermsChanged(original, created.record.coupon), false);
  assert.equal(store.list().length, 1);
  assert.equal(store.confirm(DAY, changed, 500, 'money').reason, 'already_confirmed');
});

test('history remains independent of the current coupon payload and sorts newest confirmation first', () => {
  const storage = new MemoryStorage();
  let now = new Date('2099-01-01T10:00:00.000Z');
  const store = confirmation.createStore(storage, { now: () => now });
  const first = coupon();
  const second = coupon({ home: 'Spain', away: 'France' });
  store.confirm(DAY, first, 100, 'money');
  now = new Date('2099-01-02T10:00:00.000Z');
  store.confirm('2099-01-02', second, 200, 'freebet');

  const restored = confirmation.createStore(storage);
  assert.equal(restored.list().length, 2);
  assert.equal(restored.list()[0].day, '2099-01-02');
  assert.equal(restored.list()[1].day, DAY);
  assert.equal(restored.get(DAY, coupon({ odds: 1.01 + 1 })).amount, 100);
});

test('schema v1 migrates in memory, restores old price-based keys and keeps the earliest duplicate', () => {
  const storage = new MemoryStorage();
  const original = coupon();
  const legacyCoupon = {
    type: original.type,
    bookmaker: original.bookmaker,
    odds: original.odds,
    legs: original.legs.map(leg => ({
      home: leg.home, away: leg.away, time: leg.time, label: leg.label,
      league: leg.league, odds: leg.odds,
    })),
  };
  const oldKey = JSON.stringify({ day: DAY, coupon: JSON.stringify(legacyCoupon) });
  const duplicateKey = JSON.stringify({
    day: DAY,
    coupon: JSON.stringify({ ...legacyCoupon, odds: 1.41 }),
  });
  storage.values.set(confirmation.STORAGE_KEY, JSON.stringify({
    version: 1,
    records: {
      [oldKey]: { amount: 300, betType: 'money', confirmedAt: '2099-01-01T11:00:00.000Z' },
      [duplicateKey]: { amount: 999, betType: 'freebet', confirmedAt: '2099-01-01T12:00:00.000Z' },
    },
  }));

  const store = confirmation.createStore(storage);
  assert.equal(store.isAvailable(), true);
  assert.equal(storage.writes, 0);
  assert.equal(JSON.parse(storage.getItem(confirmation.STORAGE_KEY)).version, 1);
  assert.equal(store.list().length, 1);
  const migrated = store.get(DAY, coupon({ odds: 1.75, bookmaker: 'Another BK' }));
  assert.equal(migrated.amount, 300);
  assert.equal(migrated.betType, 'money');
  assert.equal(migrated.confirmedAt, '2099-01-01T11:00:00.000Z');
  assert.equal(migrated.coupon.odds, 1.4);
  assert.equal(migrated.coupon.bookmaker, 'Fonbet');
  assert.equal(migrated.coupon.probability, null);
});

test('removing a placement is manual, persisted and does not touch other coupons', () => {
  const storage = new MemoryStorage();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const first = coupon();
  const second = coupon({ home: 'Spain', away: 'France' });
  store.confirm(DAY, first, 100, 'money');
  store.confirm(DAY, second, 200, 'freebet');
  const writes = storage.writes;
  store.get(DAY, coupon({ odds: 1.8, bookmaker: 'Another BK' }));
  store.list();
  assert.equal(storage.writes, writes);

  assert.deepEqual(store.remove(DAY, first), { ok: true });
  const restored = confirmation.createStore(storage);
  assert.equal(restored.get(DAY, first), null);
  assert.equal(restored.get(DAY, second).amount, 200);
  assert.equal(restored.list().length, 1);
});

test('blocked or corrupt storage fails closed instead of showing a false placement', () => {
  const blocked = confirmation.createStore(null);
  assert.equal(blocked.isAvailable(), false);
  assert.deepEqual(blocked.confirm(DAY, coupon(), 100, 'money'), { ok: false, reason: 'storage_unavailable' });

  const corrupt = new MemoryStorage();
  corrupt.setItem(confirmation.STORAGE_KEY, '{broken');
  const corruptStore = confirmation.createStore(corrupt);
  assert.equal(corruptStore.isAvailable(), false);
  assert.equal(corruptStore.get(DAY, coupon()), null);
});

test('a failed storage write leaves the previous visible state unchanged', () => {
  const storage = new MemoryStorage();
  storage.setItem = () => { throw new Error('quota'); };
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const result = store.confirm(DAY, coupon(), 100, 'money');
  assert.deepEqual(result, { ok: false, reason: 'storage_write_failed' });
  assert.equal(store.get(DAY, coupon()), null);
});

test('dedicated page has access control, persistent history and confirmation loaded before the board', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'coupons.html'), 'utf8');
  assert.match(html, /access-pending/);
  assert.match(html, /access-gate\.js/);
  assert.match(html, /data-confirmations="true"/);
  assert.ok(html.indexOf('coupon-confirmation.js') < html.indexOf('daily-board.js'));
  assert.match(html, /localStorage/);
  assert.match(html, /id="placed-bets"/);
  assert.match(html, /id="placed-bets-list"/);
  assert.match(html, /id="placed-bets-count"/);
  assert.match(html, /смена игрового дня эту историю не изменяют/);

  const home = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  assert.match(home, /href="\.\/coupons\.html"/);
  assert.doesNotMatch(home, /id="daily-board"/);
  assert.doesNotMatch(home, /src="daily-board\.js"/);
});
