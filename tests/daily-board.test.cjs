const assert = require('node:assert/strict');
const test = require('node:test');
const {
  MOSCOW_DAY,
  expectedMarketsForCompetition,
  GAME_SESSIONS,
  gameSessionForTime,
  groupCandidatesBySession,
  normalizeCoupon,
  renderBoard,
  renderCandidates,
  groupMatches,
  couponIdentity,
  parseCouponConfirmations,
  placedSelection,
} = require('../daily-board.js');

function coupon(overrides = {}) {
  return {
    type: 'single',
    bookmaker: 'fonbet',
    price: 1.95,
    expected_value: 0.1,
    legs: [{
      event_id: 'liga-portugal-2:123', fixture_id: 123,
      home: 'Торреенсе', away: 'Порту Б', competition: 'Лига Португалия 2',
      time: '2026-11-15T17:00:00.000Z', market: 'П1', odds_key: 'home_win',
      probability: 0.64, fair: 1.56, price: 1.95, expected_value: 0.1,
      model_probabilities: { xg: 0.64, poisson: 0.63, dixon_coles: 0.64 },
      market_ids: ['fonbet:123'], best_price: { bookmaker: 'fonbet', price: 1.95 },
      bookmaker_count: 1, comparison_count: 0,
    }],
    ...overrides,
  };
}

test('Moscow day uses a fixed UTC+3 boundary', () => {
  assert.equal(MOSCOW_DAY('2026-11-15T20:59:59.000Z'), '2026-11-15');
  assert.equal(MOSCOW_DAY('2026-11-15T21:00:00.000Z'), '2026-11-16');
  assert.equal(MOSCOW_DAY('2026-11-16T02:00:00.000Z'), '2026-11-16');
  assert.equal(MOSCOW_DAY('2026-03-29T00:30:00.000Z'), '2026-03-29');
  assert.equal(MOSCOW_DAY('2026-10-25T00:30:00.000Z'), '2026-10-25');
});

test('fixture schedules use Moscow kickoff dates rather than UTC dates', () => {
  assert.equal(MOSCOW_DAY('2026-11-20T21:00:00.000Z'), '2026-11-21');
  assert.equal(MOSCOW_DAY('2026-11-21T20:59:59.000Z'), '2026-11-21');
  assert.equal(MOSCOW_DAY('2026-11-21T21:00:00.000Z'), '2026-11-22');
});

test('four game sessions partition the Moscow day at exact boundaries', () => {
  assert.deepEqual(GAME_SESSIONS.map(session => session.id), ['morning', 'day', 'evening', 'night']);
  assert.equal(gameSessionForTime('2026-11-20T21:00:00.000Z').id, 'night');
  assert.equal(gameSessionForTime('2026-11-21T02:59:59.000Z').id, 'night');
  assert.equal(gameSessionForTime('2026-11-21T03:00:00.000Z').id, 'morning');
  assert.equal(gameSessionForTime('2026-11-21T08:59:59.000Z').id, 'morning');
  assert.equal(gameSessionForTime('2026-11-21T09:00:00.000Z').id, 'day');
  assert.equal(gameSessionForTime('2026-11-21T14:59:59.000Z').id, 'day');
  assert.equal(gameSessionForTime('2026-11-21T15:00:00.000Z').id, 'evening');
  assert.equal(gameSessionForTime('2026-11-21T20:59:59.000Z').id, 'evening');
  assert.equal(gameSessionForTime('2026-11-21T21:00:00.000Z').id, 'night');
  assert.equal(gameSessionForTime('not-a-time'), null);
});

test('candidate grouping keeps every market row in its kickoff session', () => {
  const rows = [
    { time: '2026-11-21T09:00:00.000Z', home: 'Д1', away: 'Д2', market: 'П1' },
    { time: '2026-11-21T09:00:00.000Z', home: 'Д1', away: 'Д2', market: 'X' },
    { time: '2026-11-21T03:00:00.000Z', home: 'У1', away: 'У2', market: 'П1' },
  ];
  const groups = groupCandidatesBySession(rows);
  assert.deepEqual(groups.map(group => group.id), ['morning', 'day']);
  assert.equal(groups[0].rows.length, 1);
  assert.equal(groups[1].rows.length, 2);
  assert.deepEqual(groupCandidatesBySession(null), []);
});

test('competition scope is derived from fixture metadata', () => {
  const cup = { competition: 'Кубок России', league_id: 999, fixtures: [{ league: { id: 235, name: 'Russian Cup' } }] };
  const league = { competition: 'Российская Премьер-Лига', league_id: 999, fixtures: [{ league: { id: 235, name: 'Premier League' } }] };
  assert.equal(expectedMarketsForCompetition(cup), 'all_markets');
  assert.equal(expectedMarketsForCompetition(league), 'small_markets');
  assert.equal(expectedMarketsForCompetition({ competition: 'Лига Португалия 2' }), 'small_markets');
});

test('normalizeCoupon rejects international-club and ambiguous small-market prices', () => {
  const base = coupon();
  const international = coupon({ legs: [{ ...base.legs[0], event_id: 'international-club:UEFA-CONF:1' }] });
  const aggregateOnly = coupon({ legs: [{ ...base.legs[0], odds_key: 'away_win', market: 'П2', model_probabilities: { xg: 0.5 }, bookmaker_count: 1, comparison_count: 1 }] });
  const staleModel = coupon({ legs: [{ ...base.legs[0], model_probabilities: null }] });
  const stalePrice = coupon({ legs: [{ ...base.legs[0], price: 1.9, best_price: { bookmaker: 'fonbet', price: 1.9 } }] });
  const overround = coupon({ type: 'double', legs: [base.legs[0], { ...base.legs[0], event_id: 'league:2', fixture_id: 2, home: 'Другой', away: 'Матч', market: 'X2', odds_key: 'away_or_draw', probability: 0.6, price: 1.4, expected_value: 0.1, best_price: { bookmaker: 'fonbet', price: 1.4 } }] });
  const noCurrentPrice = coupon({ legs: [{ ...base.legs[0], price: null, expected_value: null }] });
  assert.equal(normalizeCoupon(international), null);
  assert.equal(normalizeCoupon(aggregateOnly), null);
  assert.equal(normalizeCoupon(staleModel), null);
  assert.equal(normalizeCoupon(stalePrice), null);
  assert.equal(normalizeCoupon(overround), null);
  assert.equal(normalizeCoupon(noCurrentPrice), null);
  assert.equal(normalizeCoupon(base).type, 'single');
  const legacy = coupon();
  delete legacy.legs[0].model_probabilities;
  delete legacy.legs[0].expected_value;
  delete legacy.expected_value;
  assert.equal(normalizeCoupon(legacy).type, 'single');
  const withoutFreshnessMetadata = coupon();
  delete withoutFreshnessMetadata.legs[0].best_price;
  delete withoutFreshnessMetadata.legs[0].bookmaker_count;
  delete withoutFreshnessMetadata.legs[0].comparison_count;
  assert.equal(normalizeCoupon(withoutFreshnessMetadata).type, 'single');
});

test('normalizeCoupon derives sessions and rejects mixed sessions and declared mismatches', () => {
  const base = coupon();
  const normalized = normalizeCoupon(base);
  assert.equal(normalized.sessionId, 'evening');
  assert.equal(normalized.legs[0].sessionId, 'evening');
  const declaredMismatch = coupon({ session_id: 'morning' });
  assert.equal(normalizeCoupon(declaredMismatch), null);
  const mixed = coupon({
    type: 'double',
    legs: [
      base.legs[0],
      { ...base.legs[0], event_id: 'league:2', fixture_id: 2, home: 'Другой', away: 'Матч', time: '2026-11-15T09:00:00.000Z', market: 'X2', odds_key: 'away_or_draw', probability: 0.64, fair: 1.56, price: 1.8, expected_value: 0.1, best_price: { bookmaker: 'fonbet', price: 1.8 } },
    ],
  });
  assert.equal(normalizeCoupon(mixed), null);
});

test('coupon identity covers all priced legs and ignores generated coupon ids', () => {
  const single = coupon({ coupon_id: 'single-1', bookmaker: 'fonbet' });
  const renamed = coupon({ coupon_id: 'single-2', bookmaker: 'fonbet' });
  const changed = coupon({ coupon_id: 'single-1', bookmaker: 'fonbet', price: 2.01, legs: [{ ...coupon().legs[0], price: 2.01 }] });
  const double = coupon({ type: 'double', legs: [coupon().legs[0], { ...coupon().legs[0], event_id: 'league:2', fixture_id: 2, home: 'Вторая', away: 'Пара', market: 'X2', odds_key: 'away_or_draw', price: 1.5, best_price: { bookmaker: 'fonbet', price: 1.5 } }] });
  assert.equal(couponIdentity('2026-11-15', single), couponIdentity('2026-11-15', renamed));
  assert.notEqual(couponIdentity('2026-11-15', single), couponIdentity('2026-11-15', changed));
  assert.notEqual(couponIdentity('2026-11-15', single), couponIdentity('2026-11-15', double));
  assert.notEqual(couponIdentity('2026-11-15', single), couponIdentity('2026-11-16', single));
  const canonical = couponIdentity('2026-11-15', single);
  assert.equal(couponIdentity('2026-11-15', normalizeCoupon(single)), canonical);
});

test('placed confirmations are parsed only when selection is exactly placed', () => {
  const records = parseCouponConfirmations(JSON.stringify({ abc: { selection: 'placed', stake: '500', updatedAt: '2026-11-15T18:00:00.000Z' }, bad: { selection: 'prepared' } }));
  assert.equal(records.abc.stake, '500');
  assert.equal(records.bad, undefined);
  assert.equal(placedSelection(records.abc), true);
  assert.equal(placedSelection({ selection: 'prepared' }), false);
  assert.deepEqual(parseCouponConfirmations('{broken'), {});
});

test('match grouping preserves all rows and exact market evidence', () => {
  const home = {
    event_id: 'league:235:1', fixture_id: 1, time: '2026-11-21T16:00:00.000Z', home: 'Спартак', away: 'Динамо',
    competition: 'Российская Премьер-Лига', market: 'П1', odds_key: 'home_win', probability: 0.5,
    model_probabilities: { xg: 0.5, poisson: 0.49, dixon_coles: 0.5 },
    best_price: { bookmaker: 'fonbet', price: 2.1 }, bookmaker_count: 1, comparison_count: 0,
    market_ids: ['fonbet:1'], prices: { fonbet: { price: 2.1, market_id: 'fonbet:1' } },
    stats: { xg: { homeXg: 1.8, awayXg: 0.8 }, poisson: { homeXg: 1.8, awayXg: 0.8 }, dixonColes: { homeXg: 1.8, awayXg: 0.8 }, samples: { home: 10, away: 10 }, shotsOnTarget: { lambda: 12, lines: [24.5] } },
    xg: { homeXg: 1.8, awayXg: 0.8 },
  };
  const other = { ...home, market: 'X', odds_key: 'draw', probability: 0.25 };
  const awayWin = { ...home, market: 'П2', odds_key: 'away_win', probability: 0.25 };
  const sameKeyDifferentName = { ...other, market: 'Ничья' };
  const unsupported = { ...other, market: 'ТБ 2.5', odds_key: 'over_2_5' };
  const groups = groupMatches([home, other, awayWin, sameKeyDifferentName, unsupported]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].rows.length, 3);
  assert.deepEqual(groups[0].rows.map(row => row.market), ['П1', 'X', 'П2']);
  assert.equal(groupMatches([{ ...home, time: null }, { ...home, home: null }]).length, 0);
  assert.deepEqual(groupMatches(null), []);
});

test('renderCandidates and renderBoard use local formatting and no raw HTML interpolation', () => {
  const previousDocument = global.document;
  const previousStorage = global.localStorage;
  const elements = new Map();
  function element() {
    return {
      children: [], attributes: {}, dataset: {}, textContent: '', hidden: false,
      append(...nodes) { this.children.push(...nodes); },
      appendChild(node) { this.children.push(node); return node; },
      replaceChildren(...nodes) { this.children = nodes; },
      setAttribute(key, value) { this.attributes[key] = String(value); },
      addEventListener() {}, focus() {},
      classList: { toggle() {}, add() {} },
      checkVisibility() { return !this.hidden; },
      closest() { return this; },
      querySelector() { return this.children[0] || null; },
    };
  }
  global.document = { createElement: element, createDocumentFragment: element, querySelector(selector) { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); } };
  global.localStorage = { getItem() { return null; }, setItem() {} };
  try {
    const candidates = [{
      time: '2026-11-15T17:00:00.000Z', home: '<img src=x>', away: 'Гости & Co', competition: 'Лига Португалия 2', market: 'П1',
      probability: 0.65, best_price: { bookmaker: 'fonbet', price: 1.91 }, bookmaker_count: 1, comparison_count: 0,
      stats: { xg: { homeXg: 1.7, awayXg: 0.8 }, samples: { home: 10, away: 10 }, shotsOnTarget: { lambda: 12, lines: [24.5] } },
    }];
    renderCandidates(candidates);
    assert.equal(elements.get('#daily-candidate-count').textContent, '1 в 1 матче');
    assert.equal(elements.get('#daily-candidates').hidden, false);
    assert.equal(elements.get('#daily-empty').hidden, true);
    assert.equal(JSON.stringify(elements.get('#daily-candidates').children).includes('<img src=x>'), false);
    const normalized = normalizeCoupon(coupon());
    renderBoard({ day: '2026-11-15', coupons: [normalized], candidates }, '2026-11-15');
    assert.equal(elements.get('#daily-coupon-count').textContent, '1');
    assert.equal(elements.get('#daily-coupons').hidden, false);
    renderBoard({ day: '2026-11-15', coupons: [], candidates: [] }, '2026-11-15');
    assert.equal(elements.get('#daily-coupons').hidden, true);
    assert.equal(elements.get('#daily-empty').hidden, false);
    assert.equal(elements.get('#daily-coupon-count').textContent, '0');
  } finally {
    if (previousDocument === undefined) delete global.document; else global.document = previousDocument;
    if (previousStorage === undefined) delete global.localStorage; else global.localStorage = previousStorage;
  }
});
