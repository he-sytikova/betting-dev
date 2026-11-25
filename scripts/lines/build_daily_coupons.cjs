#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { parseArgs } = require('node:util');
const board = require('../../daily-board.js');
const goalsApi = require('../../goals-internal-model.js');
const cornersApi = require('../../internal-model.js');
const statsApi = require('../../stats-internal-model.js');
const ntApi = require('../../nt-internal-model.js');
const euroApi = require('../../euro-internal-model.js');
const euroStatsApi = require('../../euro-stats-internal-model.js');

const BOOKMAKERS = ['Fonbet', 'Marathonbet', 'Winline'];
const SINGLE_LIMIT = 5;
const DOUBLE_LIMIT = 2;
const DELTA_LIMIT_PP = 15;
const EPS = 1e-12;
const TOTAL_LABEL = /^(ТБ|ТМ|ИТБ1|ИТМ1|ИТБ2|ИТМ2) (\d+(?:\.\d+)?)$/;

function finiteOdds(value) {
  return Number.isFinite(value) && value > 1;
}

function isoTime(value) {
  const time = typeof value === 'number' ? value : Date.parse(value);
  return Number.isFinite(time) ? new Date(time).toISOString() : null;
}

function fairFrom1x2(odds) {
  if (!odds || !['home', 'draw', 'away'].every(key => finiteOdds(odds[key]))) return null;
  const home = 1 / odds.home;
  const draw = 1 / odds.draw;
  const away = 1 / odds.away;
  const sum = home + draw + away;
  const fair = { home: home / sum, draw: draw / sum, away: away / sum };
  return {
    ...fair,
    '1X': fair.home + fair.draw,
    '12': fair.home + fair.away,
    X2: fair.draw + fair.away,
  };
}

function fairTwoWay(selectedOdds, oppositeOdds) {
  if (!finiteOdds(selectedOdds) || !finiteOdds(oppositeOdds)) return null;
  const selected = 1 / selectedOdds;
  return selected / (selected + 1 / oppositeOdds);
}

// Рынки с ценами только для отображения (решение владельца от 24.11.2026):
// котировки показываем в претендентах, но в денежные купоны не допускаем —
// evaluatePrice по-прежнему отклоняет их как market_not_confirmed.
const DISPLAY_ONLY_TOTALS = {
  'ЖК': 'TOTAL_CARDS',
  'Фолы': 'TOTAL_FOULS',
  'Удары в створ': 'TOTAL_SHOTS_ON_TARGET',
  'Удары': 'TOTAL_SHOTS',
};

function marketForCandidate(candidate) {
  const displayOnlyFamily = candidate ? DISPLAY_ONLY_TOTALS[candidate.modelLabel] : undefined;
  if (!candidate || (!['Голы', 'Лига наций', 'Угловые', 'Еврокубки'].includes(candidate.modelLabel) && !displayOnlyFamily)) return null;
  const outcome = { П1: 'home', Х: 'draw', П2: 'away' }[candidate.label];
  if (outcome) return { kind: 'outcome', side: candidate.label, outcome };
  if (['1X', '12', 'X2'].includes(candidate.label)) {
    return { kind: 'double_chance', key: 'DC', side: candidate.label };
  }
  const match = TOTAL_LABEL.exec(candidate.label || '');
  if (!match) return null;
  const [, side, lineText] = match;
  const line = Number(lineText);
  let family = null;
  if (displayOnlyFamily) {
    if (side.startsWith('ИТ')) return null;
    family = displayOnlyFamily;
  } else if (candidate.modelLabel === 'Угловые') {
    if (side.startsWith('ИТ')) return null;
    family = 'TOTAL_CORNERS';
  } else if (side.endsWith('1')) {
    family = 'IT_HOME_GOALS';
  } else if (side.endsWith('2')) {
    family = 'IT_AWAY_GOALS';
  } else {
    family = 'TOTAL_GOALS';
  }
  const opposite = {
    ТБ: 'ТМ', ТМ: 'ТБ', ИТБ1: 'ИТМ1', ИТМ1: 'ИТБ1', ИТБ2: 'ИТМ2', ИТМ2: 'ИТБ2',
  }[side];
  return {
    kind: 'total', key: `${family}:${lineText}`, side, opposite, line,
    over: side === 'ТБ' || side.startsWith('ИТБ'),
    displayOnly: Boolean(displayOnlyFamily),
  };
}

function candidateOdds(candidate, line) {
  const market = marketForCandidate(candidate);
  if (!market || (market.kind === 'total' && Number.isInteger(market.line))) return null;
  let odds = null;
  if (market.kind === 'outcome') odds = line?.odds?.[market.outcome];
  else if (market.kind === 'double_chance') odds = line?.markets?.DC?.[market.side];
  else odds = line?.markets?.[market.key]?.[market.side];
  if (!finiteOdds(odds)) return null;
  return { odds, market_key: market.key || '1X2' };
}

// Причина отсутствия цены у претендента — для счётчика на странице купонов:
// 'line_absent'      — рынок сопоставлен, но такой линии нет ни у одного БК;
// 'price_incomplete' — линия есть, но цена недоступна: односторонний рынок
//                      или целочисленный тотал (скрыт политикой integer_two_way_line);
// 'unmapped_market'  — рынок вне маппинга (индивидуальные тоталы статистики);
// 'no_line_match'    — матч не сопоставлен ни с одной линией БК.
function noPriceReason(candidate, matchedLines) {
  const market = marketForCandidate(candidate);
  if (!market) return 'unmapped_market';
  const lines = (Array.isArray(matchedLines) ? matchedLines : []).filter(Boolean);
  if (!lines.length) return 'no_line_match';
  const hasMarket = lines.some(line => {
    if (market.kind === 'total') {
      const sides = line.markets?.[market.key];
      return Boolean(sides) && Object.values(sides).some(finiteOdds);
    }
    if (market.kind === 'outcome') {
      return ['home', 'draw', 'away'].some(key => finiteOdds(line.odds?.[key]));
    }
    const dc = line.markets?.DC;
    return Boolean(dc) && Object.values(dc).some(finiteOdds);
  });
  return hasMarket ? 'price_incomplete' : 'line_absent';
}

function candidateIdentity(candidate) {
  return `${candidate.modelKey}|${isoTime(candidate.time)}|${candidate.modelLabel}|${candidate.label}`;
}

function rejection(candidate, reason, details = {}) {
  return {
    time: isoTime(candidate.time),
    model_key: candidate.modelKey || null,
    league: candidate.league,
    home: candidate.home,
    away: candidate.away,
    model: candidate.modelLabel,
    label: candidate.label,
    p_model: candidate.probability,
    reason,
    ...details,
  };
}

function evaluatePrice(candidate, line) {
  const p = candidate?.probability;
  if (!Number.isFinite(p) || p < board.MIN_PROBABILITY - EPS || p > 1) {
    return { rejection: rejection(candidate, 'invalid_or_low_model_probability') };
  }
  const market = marketForCandidate(candidate);
  if (!market) return { rejection: rejection(candidate, 'market_not_confirmed') };
  // Display-only рынки (ЖК, фолы, удары): цена показывается в претендентах,
  // но в денежные купоны рынок не допускается — как и прежде, market_not_confirmed.
  if (market.displayOnly) return { rejection: rejection(candidate, 'market_not_confirmed', { market_key: market.key }) };
  if (market.kind === 'total' && Number.isInteger(market.line)) {
    return { rejection: rejection(candidate, 'integer_two_way_line', { market_key: market.key }) };
  }

  let odds = null;
  let fair = null;
  if (market.kind === 'total') {
    const sides = line?.markets?.[market.key];
    odds = sides?.[market.side];
    fair = fairTwoWay(odds, sides?.[market.opposite]);
    if (!finiteOdds(odds) || fair === null) {
      return { rejection: rejection(candidate, 'incomplete_market', {
        bookmaker: line?.bookmaker || null, match_id: line?.match_id || null, market_key: market.key,
      }) };
    }
  } else {
    const fair1x2 = fairFrom1x2(line?.odds);
    if (!fair1x2) {
      return { rejection: rejection(candidate, 'incomplete_1x2', {
        bookmaker: line?.bookmaker || null, match_id: line?.match_id || null,
      }) };
    }
    odds = market.kind === 'outcome' ? line.odds[market.outcome] : line?.markets?.DC?.[market.side];
    fair = fair1x2[market.kind === 'outcome' ? market.outcome : market.side];
    if (!finiteOdds(odds)) {
      return { rejection: rejection(candidate, 'market_absent', {
        bookmaker: line?.bookmaker || null, match_id: line?.match_id || null, market_key: market.key || '1X2',
      }) };
    }
  }

  const minimum = board.minimumOdds(p);
  const ev = p * odds;
  const deltaPp = (p - fair) * 100;
  const details = {
    bookmaker: line.bookmaker,
    match_id: String(line.match_id),
    market_key: market.key || '1X2',
    odds,
    fair,
    delta_pp: deltaPp,
    ev,
    o_min_exact: minimum.exact,
    o_min_step: minimum.step,
  };
  let reason = null;
  if (ev < board.EV_MIN - EPS) reason = 'ev_below_threshold';
  else if (Math.abs(deltaPp) >= DELTA_LIMIT_PP - EPS) reason = 'delta_anomaly';
  else if (market.over && p < fair - EPS) reason = 'over_below_market';
  else if (odds < minimum.step - EPS) reason = 'odds_below_minimum';
  if (reason) return { rejection: rejection(candidate, reason, details) };

  return {
    leg: {
      time: isoTime(candidate.time),
      model_key: candidate.modelKey,
      event_id: candidate.eventId || null,
      league: candidate.league,
      home: candidate.home,
      away: candidate.away,
      model: candidate.modelLabel,
      label: candidate.label,
      p_model: p,
      ...details,
    },
  };
}

function fixtureModelKeys(fixture, models) {
  const keys = new Set();
  for (const [api, model] of [
    [goalsApi, models.goals],
    [cornersApi, models.cornersCards],
    [statsApi, models.stats],
  ]) {
    const code = model ? api.resolveLeague(model, fixture.league) : null;
    if (code && api.resolveTeam(model, code, fixture.home)
        && api.resolveTeam(model, code, fixture.away)) {
      keys.add(api.canonicalFixtureKey(model, fixture.league, fixture.home, fixture.away));
    }
  }
  const competition = models.nations ? ntApi.resolveCompetition(models.nations, fixture.league) : null;
  const home = models.nations ? ntApi.resolveTeam(models.nations, fixture.home) : null;
  const away = models.nations ? ntApi.resolveTeam(models.nations, fixture.away) : null;
  if (competition && home && away) keys.add(`${competition.code}|${home.key}|${away.key}`);
  const euroCompetition = models.euro ? euroApi.resolveCompetition(models.euro, fixture.league) : null;
  const euroHome = models.euro ? euroApi.resolveTeam(models.euro, fixture.home) : null;
  const euroAway = models.euro ? euroApi.resolveTeam(models.euro, fixture.away) : null;
  if (euroCompetition && euroHome && euroAway) {
    keys.add(`${euroCompetition.code}|${euroHome.key}|${euroAway.key}`);
  }
  const esCompetition = models.euroStats ? euroStatsApi.resolveCompetition(models.euroStats, fixture.league) : null;
  const esHome = models.euroStats ? euroStatsApi.resolveTeam(models.euroStats, fixture.home) : null;
  const esAway = models.euroStats ? euroStatsApi.resolveTeam(models.euroStats, fixture.away) : null;
  if (esCompetition && esHome && esAway) {
    keys.add(`eurostats|${esCompetition.code}|${esHome.key}|${esAway.key}`);
  }
  return [...keys];
}

function rawFixtureIdentity(value) {
  return JSON.stringify([value.home, value.away, isoTime(value.time)]);
}

function buildMatchIndex(gated, matching, models) {
  const matchingByFixture = new Map();
  for (const record of Array.isArray(matching) ? matching : []) {
    const key = rawFixtureIdentity(record);
    if (!matchingByFixture.has(key)) matchingByFixture.set(key, []);
    matchingByFixture.get(key).push(record);
  }
  const index = new Map();
  for (const fixture of gated?.fixtures || []) {
    const records = matchingByFixture.get(rawFixtureIdentity(fixture)) || [];
    const time = isoTime(fixture.time);
    for (const modelKey of fixtureModelKeys(fixture, models)) {
      const key = `${modelKey}|${time}`;
      if (!index.has(key)) index.set(key, []);
      index.get(key).push(...records);
    }
  }
  return index;
}

function indexLines(snapshots) {
  const index = new Map();
  for (const bookmaker of BOOKMAKERS) {
    for (const line of snapshots?.[bookmaker]?.matches || []) {
      index.set(`${bookmaker}|${line.match_id}`, line);
    }
  }
  return index;
}

function dedupeEligible(legs) {
  const groups = new Map();
  for (const leg of legs) {
    const key = `${leg.model_key}|${leg.market_key}|${leg.label}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(leg);
  }
  const selected = [];
  const rejected = [];
  for (const group of groups.values()) {
    group.sort((a, b) => (b.odds - a.odds) || a.bookmaker.localeCompare(b.bookmaker));
    selected.push(group[0]);
    for (const loser of group.slice(1)) {
      rejected.push({
        time: loser.time, model_key: loser.model_key, league: loser.league,
        home: loser.home, away: loser.away, model: loser.model, label: loser.label,
        p_model: loser.p_model, bookmaker: loser.bookmaker, match_id: loser.match_id,
        market_key: loser.market_key, odds: loser.odds, fair: loser.fair,
        delta_pp: loser.delta_pp, ev: loser.ev,
        reason: loser.odds < group[0].odds ? 'deduplicated_lower_price' : 'deduplicated_tie',
        selected_bookmaker: group[0].bookmaker,
        selected_odds: group[0].odds,
      });
    }
  }
  return { selected, rejected };
}

function rankLegs(a, b) {
  return (b.p_model - a.p_model) || (b.ev - a.ev) || (b.odds - a.odds)
    || (Date.parse(a.time) - Date.parse(b.time)) || a.label.localeCompare(b.label, 'ru')
    || a.bookmaker.localeCompare(b.bookmaker);
}

function teamKey(value) {
  return String(value || '').normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase('ru-RU')
    .replace(/[^a-z0-9а-я]+/g, ' ').trim().replace(/\s+/g, ' ');
}

function legTeams(leg) {
  return [teamKey(leg.home), teamKey(leg.away)];
}

function couponFromLegs(legs, index, type) {
  const session = board.gameSessionForTime(legs[0]?.time);
  if (!session || legs.some(leg => board.gameSessionForTime(leg.time)?.id !== session.id)) {
    throw new Error('Every coupon must belong to exactly one game session');
  }
  const odds = legs.reduce((product, leg) => product * leg.odds, 1);
  const probability = legs.reduce((product, leg) => product * leg.p_model, 1);
  const ev = legs.reduce((product, leg) => product * leg.ev, 1);
  const minimum = legs.reduce((product, leg) => product * leg.o_min_step, 1);
  return {
    coupon_id: `${type}-${index + 1}`,
    type,
    session_id: session.id,
    bookmaker: legs[0].bookmaker,
    legs,
    odds,
    probability,
    ev,
    o_min_AB: type === 'double' ? minimum : undefined,
    status: 'prepared',
  };
}

function selectDoubles(legs, options = {}) {
  const limit = options.limit ?? DOUBLE_LIMIT;
  const used = new Set((options.usedTeams || []).map(teamKey).filter(Boolean));
  const pairs = [];
  for (let i = 0; i < legs.length; i += 1) {
    for (let j = i + 1; j < legs.length; j += 1) {
      const a = legs[i];
      const b = legs[j];
      if (a.bookmaker !== b.bookmaker || a.model_key === b.model_key) continue;
      if (board.gameSessionForTime(a.time)?.id !== board.gameSessionForTime(b.time)?.id) continue;
      if (board.gameDayKeyMsk(a.time) !== board.gameDayKeyMsk(b.time)) continue;
      const teams = [...legTeams(a), ...legTeams(b)];
      if (new Set(teams).size !== teams.length || teams.some(team => used.has(team))) continue;
      const coupon = couponFromLegs([a, b], 0, 'double');
      pairs.push({ legs: [a, b], teams, coupon });
    }
  }
  pairs.sort((x, y) => (y.coupon.probability - x.coupon.probability)
    || (y.coupon.ev - x.coupon.ev) || (y.coupon.odds - x.coupon.odds)
    || (Date.parse(x.legs[0].time) - Date.parse(y.legs[0].time))
    || x.legs[0].label.localeCompare(y.legs[0].label, 'ru'));
  const selected = [];
  for (const pair of pairs) {
    if (selected.length >= limit) break;
    if (pair.teams.some(team => used.has(team))) continue;
    selected.push(pair.coupon);
    pair.teams.forEach(team => used.add(team));
  }
  return { candidates: pairs.length, coupons: selected };
}

function selectSessionCoupons(eligible, options = {}) {
  const usedTeams = options.usedTeams || [];
  const singleLimit = options.singleLimit ?? SINGLE_LIMIT;
  const doubleLimit = options.doubleLimit ?? DOUBLE_LIMIT;
  const coupons = [];
  const sessions = {};
  let doubleCandidates = 0;
  for (const session of board.GAME_SESSIONS) {
    const legs = eligible.filter(leg => board.gameSessionForTime(leg.time)?.id === session.id).sort(rankLegs);
    const singles = legs.slice(0, singleLimit).map((leg, index) => {
      const coupon = couponFromLegs([leg], index, 'single');
      coupon.coupon_id = `${session.id}-single-${index + 1}`;
      return coupon;
    });
    const doubles = selectDoubles(legs, { usedTeams, limit: doubleLimit });
    doubles.coupons.forEach((coupon, index) => { coupon.coupon_id = `${session.id}-double-${index + 1}`; });
    coupons.push(...singles, ...doubles.coupons);
    doubleCandidates += doubles.candidates;
    sessions[session.id] = {
      eligible_legs: legs.length,
      singles: singles.length,
      doubles: doubles.coupons.length,
      double_candidates: doubles.candidates,
    };
  }
  return { coupons, sessions, doubleCandidates };
}

function rejectionCounts(rejected) {
  const counts = {};
  for (const row of rejected) counts[row.reason] = (counts[row.reason] || 0) + 1;
  return Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)));
}

function buildArtifact(input) {
  const {
    date, data, models, gated, matching, snapshots, generatedAt,
    usedTeams = [], singleLimit = SINGLE_LIMIT, doubleLimit = DOUBLE_LIMIT,
  } = input;
  const gateNow = Date.parse(gated?.generatedAt) || Date.parse(generatedAt) || Date.now();
  const candidates = board.buildCandidates(data, models, gateNow);
  const matchIndex = buildMatchIndex(gated, matching, models);
  const lines = indexLines(snapshots);
  const rejected = [];
  const priced = [];
  const candidatePrices = [];
  const candidateLines = new Map();

  for (const candidate of candidates) {
    const key = `${candidate.modelKey}|${isoTime(candidate.time)}`;
    const records = matchIndex.get(key) || [];
    if (!records.length) {
      rejected.push(rejection(candidate, 'fixture_not_gated'));
      continue;
    }
    const priceBase = {
      time: isoTime(candidate.time),
      model_key: candidate.modelKey,
      event_id: candidate.eventId || null,
      league: candidate.league,
      home: candidate.home,
      away: candidate.away,
      model: candidate.modelLabel,
      label: candidate.label,
      p_model: candidate.probability,
    };
    for (const bookmaker of BOOKMAKERS) {
      const record = records.find(row => row.bookmaker === bookmaker && row.status === 'matched')
        || records.find(row => row.bookmaker === bookmaker);
      if (!record) {
        rejected.push(rejection(candidate, 'no_line_match', { bookmaker }));
        continue;
      }
      if (record.status !== 'matched') {
        rejected.push(rejection(candidate, record.status || 'match_failed', {
          bookmaker, match_id: record.match_id || null,
        }));
        continue;
      }
      const line = lines.get(`${bookmaker}|${record.match_id}`);
      if (!line) {
        rejected.push(rejection(candidate, 'matched_line_missing', {
          bookmaker, match_id: String(record.match_id),
        }));
        continue;
      }
      const identity = candidateIdentity(candidate);
      if (!candidateLines.has(identity)) candidateLines.set(identity, []);
      candidateLines.get(identity).push(line);
      const quote = candidateOdds(candidate, line);
      if (quote) {
        candidatePrices.push({
          ...priceBase,
          ...quote,
          bookmaker,
          match_id: String(record.match_id),
        });
      }
      const result = evaluatePrice(candidate, line);
      if (result.leg) priced.push(result.leg);
      else rejected.push(result.rejection);
    }
  }

  const deduped = dedupeEligible(priced);
  rejected.push(...deduped.rejected);
  const eligible = deduped.selected.sort(rankLegs);
  const selected = selectSessionCoupons(eligible, { usedTeams, singleLimit, doubleLimit });

  // Счётчик претендентов без цены с причиной — для страницы купонов.
  const pricedIdentities = new Set(candidatePrices.map(row => (
    `${row.model_key}|${row.time}|${row.model}|${row.label}`
  )));
  const noPrice = { total: 0, line_absent: 0, price_incomplete: 0, unmapped_market: 0, no_line_match: 0 };
  for (const candidate of candidates) {
    const identity = candidateIdentity(candidate);
    if (pricedIdentities.has(identity)) continue;
    const reason = noPriceReason(candidate, candidateLines.get(identity));
    noPrice.total += 1;
    noPrice[reason] = (noPrice[reason] || 0) + 1;
  }

  const available = BOOKMAKERS.some(name => ['ok', 'partial'].includes(snapshots?.[name]?.status));
  const hasMatch = (matching || []).some(record => record.status === 'matched');
  const dataStatus = !available ? 'source_unavailable' : (!hasMatch ? 'no_line_match' : 'ok');
  const counts = rejectionCounts(rejected);
  const generated = isoTime(generatedAt) || new Date().toISOString();
  const artifact = {
    date,
    generated_at_utc: generated,
    data_status: dataStatus,
    candidate_prices: candidatePrices,
    prepared_coupons: selected.coupons,
    summary: {
      model_candidates: candidates.length,
      candidate_prices: candidatePrices.length,
      eligible_prices_before_dedup: priced.length,
      eligible_legs: eligible.length,
      double_candidates: selected.doubleCandidates,
      rejected: rejected.length,
      rejection_reasons: counts,
      no_price: noPrice,
      sessions: selected.sessions,
    },
    sources: Object.fromEntries(BOOKMAKERS.map(name => [name, {
      status: snapshots?.[name]?.status || 'source_unavailable',
      captured_at_utc: snapshots?.[name]?.captured_at_utc || null,
    }])),
  };
  return {
    artifact,
    diagnostics: {
      date,
      generated_at_utc: generated,
      data_status: dataStatus,
      model_gate_generated_at_utc: gated?.generatedAt || null,
      ...artifact.summary,
      rejected,
    },
  };
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function readJsonOptional(file) {
  return fs.existsSync(file) ? readJson(file) : null;
}

function loadModels(projectRoot) {
  return {
    goals: readJson(path.join(projectRoot, 'data/goals_markets_models.json')),
    cornersCards: readJson(path.join(projectRoot, 'data/corners_cards_models.json')),
    stats: readJson(path.join(projectRoot, 'data/stats_markets_models.json')),
    nations: readJson(path.join(projectRoot, 'data/nt_model.json')),
    euro: readJson(path.join(projectRoot, 'data/euro_cups_model.json')),
    // Появляется после первого переобучения с евро-статистикой.
    euroStats: readJsonOptional(path.join(projectRoot, 'data/euro_stats_models.json')),
  };
}

function writeJson(file, payload) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`);
  fs.renameSync(temporary, file);
}

function main(argv) {
  const { values } = parseArgs({
    args: argv,
    options: {
      data: { type: 'string' },
      acquisition: { type: 'string' },
      output: { type: 'string' },
      diagnostics: { type: 'string' },
      'used-teams': { type: 'string' },
    },
  });
  if (!values.data || !values.acquisition || !values.output) {
    throw new Error('Usage: build_daily_coupons.cjs --data data.json --acquisition <dir> --output analysis/doubles_YYYY-MM-DD.json [--diagnostics <file>] [--used-teams <json>]');
  }
  const projectRoot = path.resolve(__dirname, '../..');
  const data = readJson(values.data);
  const gated = readJson(path.join(values.acquisition, 'model-fixtures.json'));
  const matching = readJson(path.join(values.acquisition, 'matching.json'));
  const snapshots = Object.fromEntries(BOOKMAKERS.map(name => [
    name, readJson(path.join(values.acquisition, 'enriched', `${name.toLowerCase()}.json`)),
  ]));
  let usedTeams = [];
  if (values['used-teams']) {
    const payload = readJson(values['used-teams']);
    usedTeams = Array.isArray(payload) ? payload : payload.teams;
    if (!Array.isArray(usedTeams)) throw new Error('used-teams JSON must be an array or contain a teams array');
  }
  const result = buildArtifact({
    date: data.date,
    data,
    models: loadModels(projectRoot),
    gated,
    matching,
    snapshots,
    generatedAt: new Date().toISOString(),
    usedTeams,
  });
  writeJson(values.output, result.artifact);
  writeJson(values.diagnostics || path.join(values.acquisition, 'coupon-diagnostics.json'), result.diagnostics);
  process.stdout.write(`${JSON.stringify(result.artifact.summary, null, 2)}\n`);
}

if (require.main === module) main(process.argv.slice(2));

module.exports = {
  BOOKMAKERS, SINGLE_LIMIT, DOUBLE_LIMIT, DELTA_LIMIT_PP,
  fairFrom1x2, fairTwoWay, marketForCandidate, candidateOdds, evaluatePrice, noPriceReason,
  fixtureModelKeys, buildMatchIndex, indexLines, dedupeEligible,
  teamKey, selectDoubles, selectSessionCoupons, buildArtifact, loadModels,
};
