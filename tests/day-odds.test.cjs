const test = require('node:test');
const assert = require('node:assert/strict');

const dayOdds = require('../day-odds.js');
const goalsApi = require('../goals-internal-model.js');

// Минимальная модель голов: пустой вектор признаков (intercept-only Poisson),
// одна лига, две команды — достаточно для resolve/predict/ключей.
function stubGoalsModel() {
  return {
    schemaVersion: 2,
    leagueAliases: { 'england premier league': 'E0' },
    leagueNames: { E0: 'England. Premier League' },
    leagueStats: { E0: {} },
    teams: {
      E0: {
        arsenal: { name: 'Arsenal', stats: {}, matchesBefore: 99 },
        chelsea: { name: 'Chelsea', stats: {}, matchesBefore: 99 },
      },
    },
    features: [],
    imputerMedian: [],
    scalerMean: [],
    scalerScale: [],
    models: {
      homeGoals: { type: 'poisson', features: [], coefficients: [], intercept: 0 },
      awayGoals: { type: 'poisson', features: [], coefficients: [], intercept: 0 },
    },
    totalLines: [1.5, 2.5, 3.5, 4.5],
  };
}

const EURO365_MARKETS = {
  '1x2': { '1': 2.1, X: 3.4, '2': 3.6 },
  doubleChance: { '1X': 1.3, '12': 1.25, X2: 1.75 },
  totals: {
    '1.5': { over: 1.3, under: 3.4 },
    '2.5': { over: 1.95, under: 1.85 },
    '2.75': { over: 1.8, under: 2.0 },  // четвертная линия — не показываем
    '3.0': { over: 2.4, under: 1.55 },  // целая линия — не показываем
    '3.5': { over: 3.4, under: 1.3 },
  },
};

test('pickEuro365View extracts 1X2, DC and display total lines only', () => {
  const view = dayOdds.pickEuro365View(EURO365_MARKETS);
  assert.deepEqual(view.o1x2, { p1: '2.10', x: '3.40', p2: '3.60' });
  assert.deepEqual(view.dc, { '1X': '1.30', '12': '1.25', 'X2': '1.75' });
  assert.deepEqual(Object.keys(view.totals), ['1.5', '2.5', '3.5']);
  assert.equal(view.totals['2.5'].over, '1.95');
});

test('pickEuro365View drops broken prices and empty markets', () => {
  assert.equal(dayOdds.pickEuro365View(null), null);
  assert.equal(dayOdds.pickEuro365View({}), null);
  assert.equal(dayOdds.pickEuro365View({ '1x2': { '1': 1.0, X: 3.4, '2': 3.6 } }), null);
  const partial = dayOdds.pickEuro365View({ totals: { '2.5': { over: 1.9, under: 1.9 } } });
  assert.deepEqual(Object.keys(partial), ['totals']);
  assert.equal(partial.o1x2, undefined);
});

test('pickToaView keeps totals with max and carries avg for tooltips', () => {
  const view = dayOdds.pickToaView({
    totals: {
      '2.5': { avg: { over: 1.9, under: 1.92 }, max: { over: 2.05, under: 2.0 } },
      '4.5': { avg: { over: 9, under: 1.05 }, max: { over: 9.5, under: 1.07 } },
    },
  });
  assert.deepEqual(Object.keys(view.totals), ['2.5'], 'only display lines');
  assert.equal(view.totals['2.5'].over, '2.05');
  assert.equal(view.totals['2.5'].avgOver, '1.90');
  assert.equal(dayOdds.pickToaView({ totals: {} }), null);
  assert.equal(dayOdds.pickToaView(null), null);
});

test('pickFdoView uses the avg consensus only', () => {
  const view = dayOdds.pickFdoView({
    '1x2': { avg: { '1': 2.5, X: 3.3, '2': 2.8 }, max: { '1': 2.7, X: 3.5, '2': 2.9 } },
    totals: { '2.5': { avg: { over: 1.8, under: 2.0 }, max: { over: 1.85, under: 2.05 } } },
  });
  assert.deepEqual(view.o1x2, { p1: '2.50', x: '3.30', p2: '2.80' });
  assert.deepEqual(view.totals['2.5'], { over: '1.80', under: '2.00' });
  assert.equal(dayOdds.pickFdoView({ '1x2': {} }), null);
});

test('sameDayGate ties odds artifacts to the snapshot day', () => {
  assert.equal(dayOdds.sameDayGate({ date: '2026-11-05' }, '2026-11-05'), true);
  assert.equal(dayOdds.sameDayGate({ date: '2026-11-04' }, '2026-11-05'), false);
  assert.equal(dayOdds.sameDayGate({}, '2026-11-05'), false);
  assert.equal(dayOdds.sameDayGate({ date: '2026-11-05' }, null), false);
});

test('buildRows keeps only fixtures with at least one price and sorts by time', () => {
  const fixtures = [
    { time: Date.parse('2099-01-01T20:00:00Z'), league: 'L', home: 'A', away: 'B', kind: 'club', matchKey: 'k1' },
    { time: Date.parse('2099-01-01T17:00:00Z'), league: 'L', home: 'C', away: 'D', kind: 'euro', matchKey: 'k2' },
    { time: Date.parse('2099-01-01T18:00:00Z'), league: 'L', home: 'E', away: 'F', kind: 'club', matchKey: 'k3' },
  ];
  const events = { ev1: { markets: EURO365_MARKETS } };
  const eventIndex = new Map([['k1', 'ev1'], ['k3', 'ev404']]);
  const rows = dayOdds.buildRows(fixtures, { events, eventIndex, fdoIndex: new Map(), oddsApiIndex: new Map() });
  assert.equal(rows.length, 1, 'k2 has no prices, k3 event is missing — only k1 survives');
  assert.equal(rows[0].match, 'A — B');
  assert.ok(rows[0].euro365.o1x2);
});

test('buildRows merges sources by matchKey', () => {
  const fixtures = [
    { time: Date.parse('2099-01-01T20:00:00Z'), league: 'L', home: 'A', away: 'B', kind: 'club', matchKey: 'k1' },
  ];
  const rows = dayOdds.buildRows(fixtures, {
    events: {}, eventIndex: new Map(),
    fdoIndex: new Map([['k1', { '1x2': { avg: { '1': 2.5, X: 3.3, '2': 2.8 } } }]]),
    oddsApiIndex: new Map([['k1', { totals: { '2.5': { avg: { over: 1.9, under: 1.9 }, max: { over: 2.0, under: 2.0 } } } }]]),
  });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].euro365, null);
  assert.ok(rows[0].fdo.o1x2);
  assert.ok(rows[0].toa.totals['2.5']);
});

test('analyzableFixtures keeps club-model matches, drops uncovered leagues', () => {
  const model = stubGoalsModel();
  const predictions = [
    { time: '2099-01-01T20:00:00Z', league: 'England. Premier League', home: 'Arsenal', away: 'Chelsea' },
    { time: '2099-01-01T19:00:00Z', league: 'Mongolia. National Premier League', home: 'Yaks', away: 'Wolves' },
  ];
  const fixtures = dayOdds.analyzableFixtures(model, null, predictions);
  assert.equal(fixtures.length, 1);
  assert.equal(fixtures[0].kind, 'club');
  assert.equal(fixtures[0].matchKey, 'model|E0|arsenal|chelsea');
});

test('analyzableFixtures never invents: unknown team in a known league is dropped', () => {
  const model = stubGoalsModel();
  const predictions = [
    { time: '2099-01-01T20:00:00Z', league: 'England. Premier League', home: 'Arsenal', away: 'Atlantis FC' },
  ];
  assert.equal(dayOdds.analyzableFixtures(model, null, predictions).length, 0);
});
