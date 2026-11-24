const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const board = require('../daily-board.js');
const goalsApi = require('../goals-internal-model.js');

const DAY = '2099-01-01';
const NOW = Date.parse(`${DAY}T12:00:00Z`);

function goalsModel(homeLambda = 3, awayLambda = 0.2) {
  return {
    schemaVersion: 2,
    leagueAliases: { 'england premier league': 'E0' },
    leagueNames: { E0: 'England. Premier League' },
    leagueStats: { E0: {} },
    teams: {
      E0: {
        newcastle: { name: 'Newcastle', stats: {}, matchesBefore: 100 },
        everton: { name: 'Everton', stats: {}, matchesBefore: 100 },
      },
    },
    features: [],
    imputerMedian: [],
    scalerMean: [],
    scalerScale: [],
    models: {
      homeGoals: { type: 'poisson', features: [], coefficients: [], intercept: Math.log(homeLambda) },
      awayGoals: { type: 'poisson', features: [], coefficients: [], intercept: Math.log(awayLambda) },
    },
    marketLines: { totalGoals: [1.5, 2.5], teamGoals: [0.5, 1.5] },
    runtimePolicy: { minimumTeamMatches: 5, maxGoals: 12 },
  };
}

function euroModel(homeLambda = 2.4, awayLambda = 0.5) {
  return {
    schemaVersion: 2,
    competitions: [
      { code: 'CL', name: 'UEFA Champions League', names: ['UEFA Champions League', 'Champions League'] },
    ],
    teamAliases: {
      'real madrid': 'real-madrid', 'real madrid cf': 'real-madrid',
      psv: 'psv', 'psv eindhoven': 'psv',
    },
    teams: {
      'real-madrid': { name: 'Real Madrid', euro: { euroElo: 1900 } },
      psv: { name: 'PSV', euro: { euroElo: 1700 } },
    },
    features: [],
    imputerMedian: [],
    scalerMean: [],
    scalerScale: [],
    models: {
      home: { intercept: Math.log(homeLambda), coefficients: [] },
      away: { intercept: Math.log(awayLambda), coefficients: [] },
    },
    totalLines: [1.5, 2.5],
    maxGoals: 10,
    dcRho: 0,
  };
}

function clubStatsModel(models) {
  const specs = {};
  for (const [market, config] of Object.entries(models)) {
    specs[market] = {
      type: 'poisson', features: [], coefficients: [],
      intercept: Math.log(config.lambda), lines: config.lines,
    };
  }
  return {
    schemaVersion: 2,
    leagueAliases: { 'england premier league': 'E0' },
    leagueNames: { E0: 'England. Premier League' },
    leagueStats: { E0: {} },
    teams: {
      E0: {
        newcastle: { name: 'Newcastle', stats: {}, matchesBefore: 100 },
        everton: { name: 'Everton', stats: {}, matchesBefore: 100 },
      },
    },
    models: specs,
    runtimePolicy: { minimumTeamMatches: 5 },
  };
}

function loadNationsModel() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'nt_model.json'), 'utf8'));
}

function clubFixture(time = `${DAY}T18:00:00Z`) {
  return { time, league: 'England. Premier League', home: 'Newcastle United', away: 'Everton FC' };
}

function leg(overrides = {}) {
  return {
    home: 'Newcastle', away: 'Everton', league: 'Premier League',
    time: `${DAY}T18:00:00Z`, label: '1X', probability: 0.8,
    odds: 1.4, fair: 0.75, delta_pp: 5, ev: 1.12,
    bookmaker: 'Fonbet', ...overrides,
  };
}

test('Moscow day changes at 21:00 UTC, not at browser-local midnight', () => {
  assert.equal(board.dayKeyMsk(Date.parse('2026-11-16T20:59:59Z')), '2026-11-16');
  assert.equal(board.dayKeyMsk(Date.parse('2026-11-16T21:00:00Z')), '2026-11-17');
  assert.equal(board.dayKeyMsk('invalid'), null);
});

test('Moscow game day assigns 00:00–06:59:59 to the previous nights session', () => {
  assert.equal(board.gameDayKeyMsk(Date.parse('2026-11-21T20:54:59Z')), '2026-11-21');
  assert.equal(board.gameDayKeyMsk(Date.parse('2026-11-21T20:55:00Z')), '2026-11-21');
  assert.equal(board.gameDayKeyMsk(Date.parse('2026-11-21T21:00:00Z')), '2026-11-21');
  assert.equal(board.gameDayKeyMsk(Date.parse('2026-11-22T03:59:59Z')), '2026-11-21');
  assert.equal(board.gameDayKeyMsk(Date.parse('2026-11-22T04:00:00Z')), '2026-11-22');
  assert.equal(board.gameDayKeyMsk(Date.parse('2026-11-22T20:55:00Z')), '2026-11-22');
  assert.equal(board.gameDayKeyMsk('2026-11-22T03:59:59Z'), '2026-11-21');
  assert.equal(board.gameDayKeyMsk('invalid'), null);
});

test('overnight refresh retains the active snapshot after its final fixture starts', () => {
  const now = Date.parse('2026-11-22T23:36:22Z'); // 02:36:22 MSK on 23 November.
  const existing = { date: '2026-11-22', predictions: [{ time: '2026-11-22T23:30:00Z' }] };
  const exhausted = { date: '2026-11-22', generatedAt: '2026-11-22T19:30:32.956Z', predictions: [{ time: '2026-11-22T23:30:00Z' }] };
  assert.equal(board.hasFutureFixtures(exhausted, now), false);
  assert.equal(board.shouldRetainGameDaySnapshot(existing, exhausted, now), true);

  const nextDay = { date: '2026-11-23', predictions: [{ time: '2026-11-23T09:00:00Z' }] };
  assert.equal(board.shouldRetainGameDaySnapshot(existing, nextDay, now), true);

  const updated = { date: '2026-11-22', predictions: [{ time: '2026-11-23T00:00:00Z' }] };
  assert.equal(board.shouldRetainGameDaySnapshot(existing, updated, now), false);
  assert.equal(board.shouldRetainGameDaySnapshot({ date: '2026-11-21' }, exhausted, now), false);
  assert.equal(board.shouldRetainGameDaySnapshot(existing, exhausted, Date.parse('2026-11-23T04:00:00Z')), false);
});

test('five coupon sessions use exact Moscow boundaries and exclude outside kickoffs', () => {
  assert.deepEqual(board.GAME_SESSIONS.map(session => session.id), ['session1', 'session2', 'session3', 'session4', 'session5']);
  assert.equal(board.gameSessionForTime('2026-11-21T08:59:59.000Z'), null);
  assert.equal(board.gameSessionForTime('2026-11-21T09:00:00.000Z').id, 'session1');
  assert.equal(board.gameSessionForTime('2026-11-21T11:59:59.000Z').id, 'session1');
  assert.equal(board.gameSessionForTime('2026-11-21T12:00:00.000Z').id, 'session2');
  assert.equal(board.gameSessionForTime('2026-11-21T14:59:59.000Z').id, 'session2');
  assert.equal(board.gameSessionForTime('2026-11-21T15:00:00.000Z').id, 'session3');
  assert.equal(board.gameSessionForTime('2026-11-21T17:59:59.000Z').id, 'session3');
  assert.equal(board.gameSessionForTime('2026-11-21T18:00:00.000Z').id, 'session4');
  assert.equal(board.gameSessionForTime('2026-11-21T20:54:59.000Z').id, 'session4');
  assert.equal(board.gameSessionForTime('2026-11-21T20:55:00.000Z').id, 'session5');
  assert.equal(board.gameSessionForTime('2026-11-21T20:59:59.000Z').id, 'session5');
  assert.equal(board.gameSessionForTime('2026-11-21T21:00:00.000Z').id, 'session5');
  assert.equal(board.gameSessionForTime('2026-11-22T00:00:00.000Z').id, 'session5');
  assert.equal(board.gameSessionForTime('2026-11-22T03:59:59.000Z').id, 'session5');
  assert.equal(board.gameSessionForTime('2026-11-22T04:00:00.000Z'), null);
  assert.equal(board.gameSessionForTime('2026-11-21T03:00:00.000Z').id, 'session5');
  assert.equal(board.gameSessionForTime('not-a-time'), null);
});

test('candidate grouping keeps every market row in its kickoff session and drops outside kickoffs', () => {
  const rows = [
    { time: '2026-11-21T09:00:00.000Z', home: 'Д1', away: 'Д2', label: 'П1' },
    { time: '2026-11-21T09:00:00.000Z', home: 'Д1', away: 'Д2', label: 'X' },
    { time: '2026-11-21T13:00:00.000Z', home: 'В1', away: 'В2', label: 'П1' },
    { time: '2026-11-21T20:55:00.000Z', home: 'Н1', away: 'Н2', label: 'П1' },
    { time: '2026-11-22T03:00:00.000Z', home: 'Н3', away: 'Н4', label: 'X' },
    { time: '2026-11-22T04:00:00.000Z', home: 'Р1', away: 'Р2', label: 'П1' },
  ];
  const groups = board.groupCandidatesBySession(rows);
  assert.deepEqual(groups.map(group => group.id), ['session1', 'session2', 'session5']);
  assert.equal(groups[0].rows.length, 2);
  assert.equal(groups[1].rows.length, 1);
  assert.equal(groups[2].rows.length, 2);
  assert.deepEqual(board.groupCandidatesBySession(null), []);
});

test('minimumOdds uses EV 1.03 and rounds upward to a 0.01 step', () => {
  const regular = board.minimumOdds(0.8);
  assert.ok(Math.abs(regular.exact - 1.2875) < 1e-12);
  assert.equal(regular.step, 1.29);
  const certain = board.minimumOdds(1);
  assert.ok(Math.abs(certain.exact - 1.03) < 1e-12);
  assert.equal(certain.step, 1.03);
  const boundary = board.minimumOdds(1.03 / 1.47);
  assert.ok(Math.abs(boundary.exact - 1.47) < 1e-12);
  assert.equal(boundary.step, 1.47);
  assert.equal(board.minimumOdds(0.7).step, 1.48);
});

test('candidate admission uses the exact 75% model-probability boundary', () => {
  assert.equal(board.MIN_PROBABILITY, 0.75);
  const fixture = {
    time: Date.parse(`${DAY}T18:00:00Z`), key: 'model|E0|newcastle|everton',
    league: 'England. Premier League', home: 'Newcastle', away: 'Everton',
  };
  assert.equal(board.candidate(fixture, {}, 'Голы', '1X', 0.749999999999), null);
  const accepted = board.candidate(fixture, {}, 'Голы', '1X', 0.75);
  assert.ok(accepted);
  assert.equal(accepted.probability, 0.75);
  assert.equal(accepted.oMinStep, 1.38);
});

test('modelFixtures keeps future current-day matches and deduplicates by model identity', () => {
  const fixtures = board.modelFixtures(goalsApi, goalsModel(), [
    clubFixture(`${DAY}T19:00:00Z`),
    { ...clubFixture(`${DAY}T18:00:00Z`), home: 'Newcastle', away: 'Everton' },
    clubFixture(`${DAY}T11:59:59Z`),
    clubFixture('2099-01-02T18:00:00Z'),
    { ...clubFixture(), home: '', away: 'Everton' },
    { ...clubFixture(), time: 'not-a-date' },
  ], DAY, NOW);
  assert.equal(fixtures.length, 1);
  assert.equal(fixtures[0].time, Date.parse(`${DAY}T18:00:00Z`));
  assert.equal(fixtures[0].home, 'Newcastle');
});

test('buildCandidates includes only the snapshots own overnight occurrence', () => {
  const overnightTime = Date.parse('2099-01-01T21:30:00Z'); // 00:30 MSK on 2 January.
  const nextNightTime = Date.parse('2099-01-02T20:55:00Z'); // 23:55 MSK on 2 January.
  const predictions = [clubFixture('2099-01-01T21:30:00Z'), clubFixture('2099-01-02T20:55:00Z')];
  const rows = board.buildCandidates({ date: DAY, predictions }, { goals: goalsModel(), nations: loadNationsModel() }, NOW);
  assert.ok(rows.length > 0);
  assert.ok(rows.every(row => row.time === overnightTime));
  assert.ok(rows.every(row => board.gameDayKeyMsk(row.time) === DAY));
  assert.ok(rows.every(row => board.gameSessionForTime(row.time)?.id === 'session5'));

  const nextNight = board.buildCandidates(
    { date: '2099-01-02', predictions }, { goals: goalsModel(), nations: loadNationsModel() }, Date.parse('2099-01-02T12:00:00Z'),
  );
  assert.ok(nextNight.length > 0);
  assert.ok(nextNight.every(row => row.time === nextNightTime));
  assert.ok(nextNight.every(row => board.gameDayKeyMsk(row.time) === '2099-01-02'));
});

test('nationsFixtures rejects unknown teams and competitions outside UEFA Nations League', () => {
  const nations = loadNationsModel();
  const fixtures = board.nationsFixtures(nations, [
    { time: `${DAY}T20:00:00Z`, league: 'UEFA Nations League', home: 'Spain', away: 'France' },
    { time: `${DAY}T20:00:00Z`, league: 'UEFA Nations League', home: 'Spain', away: 'Atlantis XI' },
    { time: `${DAY}T20:00:00Z`, league: 'UEFA Champions League', home: 'Spain', away: 'France' },
  ], DAY, NOW);
  assert.equal(fixtures.length, 1);
  assert.equal(fixtures[0].home, 'Spain');
});

test('euroFixtures accepts UEFA club cups, dedupes spellings and rejects lookalikes', () => {
  const fixtures = board.euroFixtures(euroModel(), [
    { time: `${DAY}T18:00:00Z`, league: 'UEFA Champions League', home: 'Real Madrid', away: 'PSV' },
    { time: `${DAY}T18:00:00Z`, league: 'Champions League', home: 'Real Madrid CF', away: 'PSV Eindhoven' },
    { time: `${DAY}T18:00:00Z`, league: 'AFC Champions League Elite', home: 'Real Madrid', away: 'PSV' },
    { time: `${DAY}T18:00:00Z`, league: 'UEFA Champions League. Team vs Player', home: 'Real Madrid', away: 'PSV' },
    { time: `${DAY}T18:00:00Z`, league: 'UEFA Champions League', home: 'Real Madrid', away: 'Unknown FC' },
    { time: `${DAY}T20:00:00Z`, league: 'UEFA Champions League', home: 'PSV', away: 'Real Madrid' },
  ], DAY, NOW);
  assert.equal(fixtures.length, 2);
  assert.deepEqual(fixtures[0].key, 'CL|real-madrid|psv');
  assert.deepEqual(fixtures[1].key, 'CL|psv|real-madrid');
  assert.equal(board.euroFixtures(null, [{ time: `${DAY}T18:00:00Z`, league: 'UEFA Champions League', home: 'Real Madrid', away: 'PSV' }], DAY, NOW).length, 0);
});

test('buildCandidates adds euro cup candidates only when the euro model is present', () => {
  const data = { date: DAY, predictions: [
    { time: `${DAY}T18:00:00Z`, league: 'UEFA Champions League', home: 'Real Madrid', away: 'PSV' },
  ] };
  const emptyNations = { competitions: [], teamAliases: {}, teams: {} };
  const withoutEuro = board.buildCandidates(data, { goals: null, nations: emptyNations }, NOW);
  assert.equal(withoutEuro.length, 0);
  const rows = board.buildCandidates(data, { goals: null, nations: emptyNations, euro: euroModel() }, NOW);
  assert.ok(rows.length > 0);
  assert.ok(rows.every(row => row.modelLabel === 'Еврокубки'));
  assert.ok(rows.every(row => row.probability >= board.MIN_PROBABILITY && row.probability <= 1));
  assert.ok(rows.every(row => row.modelKey === 'CL|real-madrid|psv'));
  assert.ok(rows.some(row => row.label === 'П1'));
  assert.ok(rows.some(row => /^Т[БМ] \d/.test(row.label)));
  assert.ok(rows.every(row => row.lineStatus === 'not_checked'));
});

test('buildCandidates covers all active model families and preserves market lines', () => {
  const data = {
    date: DAY,
    predictions: [
      clubFixture(),
      { time: `${DAY}T20:00:00Z`, league: 'UEFA Nations League', home: 'Spain', away: 'France' },
      { time: `${DAY}T18:00:00Z`, league: 'Unknown League', home: 'Unknown A', away: 'Unknown B' },
    ],
  };
  const rows = board.buildCandidates(data, {
    goals: goalsModel(),
    cornersCards: clubStatsModel({
      corners: { lambda: 12, lines: [8.5, 10.5] },
      yellowCards: { lambda: 2.5, lines: [2.5, 3.5] },
    }),
    stats: clubStatsModel({
      fouls: { lambda: 25, lines: [20.5] },
      shots: { lambda: 30, lines: [24.5] },
      shotsOnTarget: { lambda: 12, lines: [8.5] },
    }),
    nations: loadNationsModel(),
  }, NOW);

  assert.deepEqual(new Set(rows.map(row => row.modelLabel)), new Set([
    'Голы', 'Угловые', 'ЖК', 'Фолы', 'Удары', 'Удары в створ', 'Лига наций',
  ]));
  for (const label of ['П1', '1X', 'ТБ 1.5', 'ИТБ1 0.5', 'ИТМ2 0.5']) {
    assert.ok(rows.some(row => row.modelLabel === 'Голы' && row.label === label), label);
  }
  assert.ok(rows.some(row => row.modelLabel === 'Угловые' && row.label === 'ТБ 8.5'));
  assert.ok(rows.some(row => row.modelLabel === 'ЖК' && row.label === 'ТМ 3.5'));
  assert.ok(rows.some(row => row.modelLabel === 'Лига наций'));
  assert.ok(rows.every(row => row.probability >= board.MIN_PROBABILITY && row.probability <= 1));
  assert.ok(rows.every(row => row.lineStatus === 'not_checked'));
  assert.ok(rows.every(row => row.oMinStep >= row.oMinExact - 1e-12));
  assert.ok(rows.every(row => row.time > NOW && board.dayKeyMsk(row.time) === DAY));
  assert.ok(rows.every(row => !String(row.home).includes('Unknown')));
});

test('candidate market names cover every active family in stable order and allow future markets', () => {
  const rows = [
    ...board.CANDIDATE_MARKETS.slice().reverse().map(modelLabel => ({ modelLabel })),
    { modelLabel: 'Голы' },
    { modelLabel: 'Новый рынок' },
    { modelLabel: '' },
    null,
  ];
  assert.deepEqual(board.candidateMarketNames(rows), [...board.CANDIDATE_MARKETS, 'Новый рынок']);
  assert.deepEqual(board.candidateMarketNames(null), []);
});

test('market filtering selects the exact family without changing the source rows', () => {
  const rows = [
    { modelLabel: 'Голы', label: 'П1' },
    { modelLabel: 'Угловые', label: 'ТБ 8.5' },
    { modelLabel: 'Голы', label: 'ТБ 2.5' },
  ];
  const snapshot = JSON.parse(JSON.stringify(rows));
  const all = board.filterCandidatesByMarket(rows);
  assert.notEqual(all, rows);
  assert.deepEqual(all, rows);
  assert.deepEqual(board.filterCandidatesByMarket(rows, 'Голы').map(row => row.label), ['П1', 'ТБ 2.5']);
  assert.deepEqual(board.filterCandidatesByMarket(rows, 'ЖК'), []);
  assert.deepEqual(board.filterCandidatesByMarket(null, 'Голы'), []);
  assert.deepEqual(rows, snapshot);
});

test('bookmaker filters stay available without prices and combined filters select every matching quote', () => {
  const rows = [
    {
      modelLabel: 'Угловые', label: 'ТБ 8.5',
      bookmakerPrices: { Marathonbet: { bookmaker: 'Marathonbet', odds: 1.5 } },
    },
    {
      modelLabel: 'Голы', label: 'ТБ 2.5',
      bookmakerPrices: { Marathonbet: { bookmaker: 'Marathonbet', odds: 1.8 } },
    },
    {
      modelLabel: 'Угловые', label: 'ТМ 10.5',
      bookmakerPrices: { Winline: { bookmaker: 'Winline', odds: 1.7 } },
    },
    { modelLabel: 'Угловые', label: 'ТБ 9.5', bestBookmaker: 'Marathonbet', bestOdds: 1.6 },
    { modelLabel: 'Угловые', label: 'ТБ 11.5', bestBookmaker: 'Future BK', bestOdds: 1.9 },
    null,
  ];
  const snapshot = JSON.parse(JSON.stringify(rows));
  assert.deepEqual(board.candidateBookmakerNames(rows), ['Fonbet', 'Marathonbet', 'Winline', 'Future BK']);
  assert.deepEqual(board.candidateBookmakerNames([{ modelLabel: 'Голы', label: 'П1' }]),
    ['Fonbet', 'Marathonbet', 'Winline']);
  assert.deepEqual(board.candidateBookmakerNames([]), []);
  assert.deepEqual(board.candidateBookmakerNames(null), []);
  const corners = board.filterCandidatesByMarket(rows, 'Угловые');
  assert.deepEqual(board.filterCandidatesByBookmaker(corners, 'Marathonbet').map(row => row.label), ['ТБ 8.5', 'ТБ 9.5']);
  assert.deepEqual(board.filterCandidatesByBookmaker(corners, 'Fonbet'), []);
  assert.deepEqual(board.filterCandidatesByBookmaker(null, 'Marathonbet'), []);
  assert.deepEqual(board.candidateBookmakerPrice(rows[4], 'Future BK'), { bookmaker: 'Future BK', odds: 1.9 });
  assert.deepEqual(rows, snapshot);
});

test('groupCandidatesByMatch collapses markets into exact match events without changing their order', () => {
  const first = {
    time: Date.parse(`${DAY}T18:00:00Z`), home: 'Newcastle', away: 'Everton',
    league: 'England. Premier League', modelKey: 'goals|E0|newcastle|everton', label: '1X',
  };
  const rows = [
    first,
    { ...first, modelKey: 'corners|E0|newcastle|everton', label: 'ТБ 8.5' },
    { ...first, time: Date.parse(`${DAY}T20:00:00Z`), label: 'X2' },
  ];
  const groups = board.groupCandidatesByMatch(rows);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups.map(group => group.rows.length), [2, 1]);
  assert.deepEqual(groups[0].rows.map(row => row.label), ['1X', 'ТБ 8.5']);
  assert.equal(groups[0].home, 'Newcastle');
  assert.equal(groups[1].time, Date.parse(`${DAY}T20:00:00Z`));
  assert.deepEqual(board.groupCandidatesByMatch([]), []);
  assert.equal(rows.length, 3);
});

test('candidate price keys canonicalize time and field aliases without mixing identities', () => {
  const row = {
    time: Date.parse(`${DAY}T18:00:00Z`), modelKey: 'model|E0|newcastle|everton',
    modelLabel: 'Голы', label: '1X',
  };
  const key = board.candidatePriceKey(row);
  assert.equal(key, board.candidatePriceKey({
    time: `${DAY}T18:00:00.000Z`, model_key: row.modelKey, model: 'Голы', label: '1X',
  }));
  assert.equal(key, board.candidatePriceKey({ ...row, time: new Date(row.time) }));
  assert.notEqual(key, board.candidatePriceKey({ ...row, label: '12' }));
  assert.notEqual(key, board.candidatePriceKey({ ...row, modelLabel: 'Лига наций' }));
  assert.notEqual(key, board.candidatePriceKey({ ...row, modelKey: 'other' }));
  assert.notEqual(key, board.candidatePriceKey({ ...row, time: Date.parse(`${DAY}T19:00:00Z`) }));
  assert.equal(board.candidatePriceKey({ ...row, time: 'invalid' }), null);
  assert.equal(board.candidatePriceKey({ ...row, label: '' }), null);
});

test('candidate price merge selects the best valid quote and never mutates candidate rows', () => {
  const row = {
    time: Date.parse(`${DAY}T18:00:00Z`), modelKey: 'model|E0|newcastle|everton',
    modelLabel: 'Голы', label: '1X', probability: 0.8,
  };
  const rows = [row, { ...row, label: '12' }];
  const snapshot = JSON.parse(JSON.stringify(rows));
  const quote = {
    time: `${DAY}T18:00:00.000Z`, model_key: row.modelKey, model: 'Голы', label: '1X',
  };
  const merged = board.mergeCandidatePrices(rows, { candidate_prices: [
    { ...quote, bookmaker: 'Fonbet', odds: 1.4 },
    { ...quote, bookmaker: 'Winline', odds: 1.55 },
    { ...quote, bookmaker: 'Marathonbet', odds: 1.5 },
    { ...quote, bookmaker: 'Marathonbet', odds: 1.45 },
    { ...quote, bookmaker: 'Unknown', odds: 9 },
    { ...quote, bookmaker: 'Winline', odds: 1 },
    { ...quote, bookmaker: 'Winline', odds: Infinity },
  ] });
  assert.equal(merged[0].bestOdds, 1.55);
  assert.equal(merged[0].bestBookmaker, 'Winline');
  assert.deepEqual(merged[0].bookmakerPrices, {
    Fonbet: { bookmaker: 'Fonbet', odds: 1.4 },
    Winline: { bookmaker: 'Winline', odds: 1.55 },
    Marathonbet: { bookmaker: 'Marathonbet', odds: 1.5 },
  });
  assert.equal(merged[1].bestOdds, null);
  assert.equal(merged[1].bestBookmaker, null);
  assert.deepEqual(merged[1].bookmakerPrices, {});
  assert.deepEqual(rows, snapshot);
  assert.notEqual(merged[0], row);
  assert.deepEqual(board.mergeCandidatePrices(rows, {}), rows.map(item => ({
    ...item, bestOdds: null, bestBookmaker: null, bookmakerPrices: {},
  })));
  assert.deepEqual(board.mergeCandidatePrices(null, { candidate_prices: [] }), []);
});

test('buildCandidates returns no candidates for a malformed snapshot', () => {
  assert.deepEqual(board.buildCandidates({}, {}, NOW), []);
  assert.deepEqual(board.buildCandidates({ date: DAY, predictions: null }, {}, NOW), []);
});

test('normalizeCoupon derives single-coupon metrics and always marks it prepared', () => {
  const coupon = board.normalizeCoupon({ ...leg(), status: 'placed' }, 0);
  assert.equal(coupon.type, 'single');
  assert.equal(coupon.bookmaker, 'Fonbet');
  assert.equal(coupon.odds, 1.4);
  assert.equal(coupon.probability, 0.8);
  assert.equal(coupon.ev, 1.12);
  assert.equal(coupon.status, 'prepared');
});

test('normalizeCoupon derives double odds, probability and EV from same-bookmaker legs', () => {
  const coupon = board.normalizeCoupon({
    coupon_id: 'double-1',
    legs: [leg(), leg({ home: 'Spain', away: 'France', probability: 0.75, odds: 1.5, ev: null })],
  }, 0);
  assert.equal(coupon.id, 'double-1');
  assert.equal(coupon.type, 'double');
  assert.ok(Math.abs(coupon.odds - 2.1) < 1e-12);
  assert.ok(Math.abs(coupon.probability - 0.6) < 1e-12);
  assert.ok(Math.abs(coupon.ev - 1.26) < 1e-12);
  assert.equal(coupon.status, 'prepared');
});

test('normalizeCoupon derives sessions and rejects mixed, mismatched and outside sessions', () => {
  const first = board.normalizeCoupon({ ...leg({ time: `${DAY}T09:00:00Z`, session_id: 'session1' }) }, 0);
  assert.equal(first.sessionId, 'session1');
  assert.equal(first.legs[0].sessionId, 'session1');
  assert.equal(board.normalizeCoupon({ ...leg(), session_id: 'session1' }, 0), null);
  assert.equal(board.normalizeCoupon({ ...leg({ time: `${DAY}T03:00:00Z` }) }, 0).sessionId, 'session5');
  assert.equal(board.normalizeCoupon({ ...leg({ time: `${DAY}T04:00:00Z` }) }, 0), null);
  const mixed = board.normalizeCoupon({
    legs: [leg(), leg({ home: 'Spain', away: 'France', time: `${DAY}T09:00:00Z` })],
  }, 0);
  assert.equal(mixed, null);
});

test('normalizeCoupon rejects a double assembled from different bookmakers', () => {
  const coupon = board.normalizeCoupon({
    legs: [leg(), leg({ home: 'Spain', away: 'France', bookmaker: 'Marathonbet' })],
  }, 0);
  assert.equal(coupon, null);
});

test('normalizeCoupons skips malformed records and reports rejected indexes', () => {
  const result = board.normalizeCoupons({ prepared_coupons: [
    leg(),
    { legs: [leg(), leg({ bookmaker: 'Marathonbet' })] },
    { home: 'Only home', odds: 1.5, bookmaker: 'Fonbet' },
    { legs: [leg(), leg(), leg()] },
  ] });
  assert.equal(result.coupons.length, 1);
  assert.deepEqual(result.rejected, [1, 2, 3]);
});

test('couponEmptyMessage distinguishes processing states and an empty processed day', () => {
  assert.match(board.couponEmptyMessage({ data_status: 'not_processed' }), /ещё не обработаны/);
  assert.match(board.couponEmptyMessage({ data_status: 'source_unavailable' }), /недоступен/);
  assert.match(board.couponEmptyMessage({ data_status: 'no_line_match' }), /не сопоставлены/);
  assert.match(board.couponEmptyMessage({ data_status: 'ok' }), /пропуск/);
  assert.match(board.couponEmptyMessage({ data_status: 'stale_snapshot' }), /не считается проставленным/);
});
