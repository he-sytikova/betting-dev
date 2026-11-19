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

test('stake amount accepts ordinary decimal input and rejects unsafe or ambiguous values', () => {
  assert.equal(confirmation.parseAmount('100'), 100);
  assert.equal(confirmation.parseAmount(' 1 250,50 '), 1250.5);
  assert.equal(confirmation.parseAmount('.25'), 0.25);
  assert.equal(confirmation.parseAmount('0,01'), 0.01);
  for (const value of ['', ' ', '0', '0,00', '-1', '+1', '1e3', 'Infinity', 'NaN', 'abc', '1,234', '1.2.3', '1,2,3']) {
    assert.equal(confirmation.parseAmount(value), null, value);
  }
});

test('coupon identity is stable and includes bookmaker, composition, start times and prices', () => {
  const original = coupon();
  assert.equal(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ coupon_id: 'another-id' })));
  assert.notEqual(confirmation.confirmationKey(DAY, original), confirmation.confirmationKey('2099-01-02', original));
  assert.notEqual(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ odds: 1.41 })));
  assert.notEqual(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ bookmaker: 'Marathonbet' })));
  assert.notEqual(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ time: `${DAY}T19:00:00Z` })));
  assert.notEqual(confirmation.couponIdentity(original), confirmation.couponIdentity(coupon({ label: '12' })));
  assert.equal(confirmation.confirmationKey('not-a-day', original), null);
});

test('a new store has no placements and performs no automatic writes', () => {
  const storage = new MemoryStorage();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  assert.equal(store.isAvailable(), true);
  assert.equal(store.get(DAY, coupon()), null);
  assert.equal(storage.writes, 0);
  assert.equal(storage.getItem(confirmation.STORAGE_KEY), null);
});

test('manual money confirmation persists and restores after a reload', () => {
  const storage = new MemoryStorage();
  const current = coupon();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const result = store.confirm(DAY, current, '1 000,50', 'money');
  assert.equal(result.ok, true);
  assert.deepEqual(result.record, {
    amount: 1000.5,
    betType: 'money',
    confirmedAt: FIXED_NOW.toISOString(),
  });
  assert.equal(storage.writes, 1);

  const restored = confirmation.createStore(storage).get(DAY, current);
  assert.deepEqual(restored, result.record);
  assert.equal(confirmation.createStore(storage).get('2099-01-02', current), null);
});

test('freebet and money are separate validated bet types', () => {
  const storage = new MemoryStorage();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const current = coupon();
  assert.equal(store.confirm(DAY, current, 100, 'freebet').record.betType, 'freebet');
  assert.equal(store.confirm(DAY, current, 200, 'money').record.betType, 'money');
  const writes = storage.writes;
  assert.deepEqual(store.confirm(DAY, current, 200, 'other'), { ok: false, reason: 'invalid_bet_type' });
  assert.equal(store.get(DAY, current).betType, 'money');
  assert.equal(storage.writes, writes);
});

test('invalid amount never creates or overwrites a placement', () => {
  const storage = new MemoryStorage();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const current = coupon();
  assert.deepEqual(store.confirm(DAY, current, '0', 'money'), { ok: false, reason: 'invalid_amount' });
  assert.equal(storage.writes, 0);
  store.confirm(DAY, current, '500', 'money');
  const writes = storage.writes;
  assert.deepEqual(store.confirm(DAY, current, 'abc', 'money'), { ok: false, reason: 'invalid_amount' });
  assert.equal(store.get(DAY, current).amount, 500);
  assert.equal(storage.writes, writes);
});

test('changing published odds invalidates the old local confirmation key', () => {
  const storage = new MemoryStorage();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const original = coupon();
  store.confirm(DAY, original, '500', 'money');
  assert.equal(store.get(DAY, coupon({ odds: 1.41 })), null);
  assert.equal(store.get(DAY, original).amount, 500);
});

test('removing a placement is persisted without touching other coupons', () => {
  const storage = new MemoryStorage();
  const store = confirmation.createStore(storage, { now: () => FIXED_NOW });
  const first = coupon();
  const second = coupon({ home: 'Spain', away: 'France' });
  store.confirm(DAY, first, 100, 'money');
  store.confirm(DAY, second, 200, 'freebet');
  assert.deepEqual(store.remove(DAY, first), { ok: true });
  const restored = confirmation.createStore(storage);
  assert.equal(restored.get(DAY, first), null);
  assert.equal(restored.get(DAY, second).amount, 200);
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

test('dedicated page has access control and loads confirmation before the board renderer', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'coupons.html'), 'utf8');
  assert.match(html, /access-pending/);
  assert.match(html, /access-gate\.js/);
  assert.match(html, /data-confirmations="true"/);
  assert.ok(html.indexOf('coupon-confirmation.js') < html.indexOf('daily-board.js'));
  assert.match(html, /localStorage/);
  assert.match(html, /не изменяет/);

  const home = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  assert.match(home, /href="\.\/coupons\.html"/);
  assert.doesNotMatch(home, /id="daily-board"/);
  assert.doesNotMatch(home, /src="daily-board\.js"/);
});
