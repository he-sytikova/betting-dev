/* Current Moscow-game-day candidates and prepared coupons for the dev home page. */
(function (root) {
  'use strict';

  const isNode = typeof module !== 'undefined' && module.exports;
  const goalsApi = isNode ? require('./goals-internal-model.js') : root.GoalsInternalModel;
  const cornersApi = isNode ? require('./internal-model.js') : root.InternalModel;
  const statsApi = isNode ? require('./stats-internal-model.js') : root.StatsInternalModel;
  const ntApi = isNode ? require('./nt-internal-model.js') : root.NtInternalModel;
  const euroApi = isNode ? require('./euro-internal-model.js') : root.EuroInternalModel;
  const euroStatsApi = isNode ? require('./euro-stats-internal-model.js') : root.EuroStatsInternalModel;

  const MOSCOW_OFFSET_MS = 180 * 60000;
  const MIN_PROBABILITY = 0.75;
  const EV_MIN = 1.03;
  const GAME_SESSIONS = Object.freeze([
    Object.freeze({ id: 'session1', label: 'Сессия 1', timeLabel: '12:00–15:00', startMinute: 720 }),
    Object.freeze({ id: 'session2', label: 'Сессия 2', timeLabel: '15:00–18:00', startMinute: 900 }),
    Object.freeze({ id: 'session3', label: 'Сессия 3', timeLabel: '18:00–21:00', startMinute: 1080 }),
    Object.freeze({ id: 'session4', label: 'Сессия 4', timeLabel: '21:00–23:55', startMinute: 1260 }),
    Object.freeze({ id: 'session5', label: 'Сессия 5', timeLabel: '23:55–07:00', startMinute: 1435 }),
  ]);
  const CANDIDATE_MARKETS = Object.freeze([
    'Голы', 'Угловые', 'ЖК', 'Фолы', 'Удары', 'Удары в створ', 'Лига наций', 'Еврокубки',
  ]);
  const CANDIDATE_BOOKMAKERS = Object.freeze(['Fonbet', 'Marathonbet', 'Winline', 'Tennisi']);

  function gameSessionForTime(value) {
    const time = value instanceof Date ? value.getTime() : (typeof value === 'number' ? value : Date.parse(value));
    if (!Number.isFinite(time)) return null;
    const moscow = new Date(time + MOSCOW_OFFSET_MS);
    const minute = moscow.getUTCHours() * 60 + moscow.getUTCMinutes();
    if (minute >= 1435 || minute < 420) return GAME_SESSIONS[4];
    if (minute < 720) return null;
    if (minute < 900) return GAME_SESSIONS[0];
    if (minute < 1080) return GAME_SESSIONS[1];
    if (minute < 1260) return GAME_SESSIONS[2];
    return GAME_SESSIONS[3];
  }

  function dayKeyMsk(value) {
    const time = value instanceof Date ? value.getTime() : Number(value);
    if (!Number.isFinite(time)) return null;
    return new Date(time + MOSCOW_OFFSET_MS).toISOString().slice(0, 10);
  }

  function gameDayKeyMsk(value) {
    const time = value instanceof Date ? value.getTime() : (typeof value === 'number' ? value : Date.parse(value));
    if (!Number.isFinite(time)) return null;
    const moscow = new Date(time + MOSCOW_OFFSET_MS);
    if (moscow.getUTCHours() * 60 + moscow.getUTCMinutes() < 420) {
      moscow.setUTCDate(moscow.getUTCDate() - 1);
    }
    return moscow.toISOString().slice(0, 10);
  }

  function hasFutureFixtures(data, value) {
    const now = value instanceof Date ? value.getTime() : (typeof value === 'number' ? value : Date.parse(value));
    if (!Number.isFinite(now) || !Array.isArray(data?.predictions)) return false;
    return data.predictions.some(item => {
      const time = Date.parse(item.time);
      return Number.isFinite(time) && time > now;
    });
  }

  function shouldRetainGameDaySnapshot(existing, downloaded, value) {
    const now = value instanceof Date ? value.getTime() : (typeof value === 'number' ? value : Date.parse(value));
    if (!Number.isFinite(now)) return false;
    const currentGameDay = gameDayKeyMsk(now);
    const isAfterMidnight = currentGameDay !== dayKeyMsk(now);
    return isAfterMidnight && existing?.date === currentGameDay
      && (downloaded?.date !== currentGameDay || !hasFutureFixtures(downloaded, now));
  }

  function minimumOdds(probability) {
    const exact = EV_MIN / probability;
    return { exact, step: Math.ceil((exact - 1e-12) * 100) / 100 };
  }

  function modelFixtures(api, model, predictions, day, now) {
    const fixtures = new Map();
    for (const row of Array.isArray(predictions) ? predictions : []) {
      const time = Date.parse(row.time);
      if (!Number.isFinite(time) || time <= now || gameDayKeyMsk(time) !== day) continue;
      if (!row.home || !row.away) continue;
      const key = api.canonicalFixtureKey(model, row.league, row.home, row.away);
      if (!fixtures.has(key) || time < fixtures.get(key).time) {
        fixtures.set(key, { key, time, league: row.league || '—', home: row.home, away: row.away, eventId: row.eventId || row.event_id || null });
      }
    }
    return [...fixtures.values()].sort((a, b) => a.time - b.time);
  }

  function nationsFixtures(model, predictions, day, now) {
    const fixtures = new Map();
    for (const row of Array.isArray(predictions) ? predictions : []) {
      const time = Date.parse(row.time);
      if (!Number.isFinite(time) || time <= now || gameDayKeyMsk(time) !== day) continue;
      const competition = ntApi.resolveCompetition(model, row.league);
      const home = ntApi.resolveTeam(model, row.home);
      const away = ntApi.resolveTeam(model, row.away);
      if (!competition || !home || !away) continue;
      const key = `${competition.code}|${home.key}|${away.key}`;
      if (!fixtures.has(key) || time < fixtures.get(key).time) {
        fixtures.set(key, { key, time, league: row.league, home: row.home, away: row.away, eventId: row.eventId || row.event_id || null });
      }
    }
    return [...fixtures.values()].sort((a, b) => a.time - b.time);
  }

  function candidate(fixture, prediction, modelLabel, label, probability) {
    if (!Number.isFinite(probability) || probability < MIN_PROBABILITY || probability > 1) return null;
    const min = minimumOdds(probability);
    return {
      time: fixture.time,
      modelKey: fixture.key,
      eventId: fixture.eventId || null,
      league: prediction.leagueLabel || prediction.league || fixture.league,
      home: prediction.homeModel || prediction.home || fixture.home,
      away: prediction.awayModel || prediction.away || fixture.away,
      modelLabel,
      label,
      probability,
      oMinExact: min.exact,
      oMinStep: min.step,
      lineStatus: 'not_checked',
    };
  }

  function addTotal(rows, fixture, prediction, modelLabel, prefix, line, over, under) {
    const overRow = candidate(fixture, prediction, modelLabel, `${prefix} ${line}`, over);
    const underLabel = prefix.startsWith('ИТБ') ? prefix.replace('ИТБ', 'ИТМ') : prefix.replace('ТБ', 'ТМ');
    const underRow = candidate(fixture, prediction, modelLabel, `${underLabel} ${line}`, under);
    if (overRow) rows.push(overRow);
    if (underRow) rows.push(underRow);
  }

  function goalsCandidates(model, predictions, day, now) {
    const rows = [];
    for (const fixture of modelFixtures(goalsApi, model, predictions, day, now)) {
      const prediction = goalsApi.predict(model, fixture.league, fixture.home, fixture.away);
      if (!prediction) continue;
      const outcomes = prediction.outcomes || {};
      for (const [label, key] of [['П1', 'p1'], ['Х', 'x'], ['П2', 'p2'], ['1X', 'dc1x'], ['12', 'dc12'], ['X2', 'dcx2']]) {
        const row = candidate(fixture, prediction, 'Голы', label, outcomes[key]);
        if (row) rows.push(row);
      }
      for (const total of prediction.totalGoals || []) {
        addTotal(rows, fixture, prediction, 'Голы', 'ТБ', total.line, total.over, total.under);
      }
      for (const [side, prefix] of [['home', 'ИТБ1'], ['away', 'ИТБ2']]) {
        for (const total of prediction.teamGoals?.[side] || []) {
          addTotal(rows, fixture, prediction, 'Голы', prefix, total.line, total.over, total.under);
        }
      }
    }
    return rows;
  }

  function clubMarketCandidates(api, model, predictions, day, now, marketLabels) {
    const rows = [];
    for (const fixture of modelFixtures(api, model, predictions, day, now)) {
      const prediction = api.predict(model, fixture.league, fixture.home, fixture.away);
      if (!prediction) continue;
      for (const [market, modelLabel] of Object.entries(marketLabels)) {
        for (const total of prediction[market]?.lines || []) {
          addTotal(rows, fixture, prediction, modelLabel, 'ТБ', total.line, total.over, total.under);
        }
      }
    }
    return rows;
  }

  function nationsCandidates(model, predictions, day, now) {
    const rows = [];
    for (const fixture of nationsFixtures(model, predictions, day, now)) {
      const prediction = ntApi.predict(model, fixture.league, fixture.home, fixture.away);
      if (!prediction) continue;
      const markets = prediction.markets || {};
      for (const [label, key] of [['П1', 'p1'], ['Х', 'x'], ['П2', 'p2'], ['1X', 'dc1x'], ['12', 'dc12'], ['X2', 'dcx2']]) {
        const row = candidate(fixture, { ...prediction, leagueLabel: fixture.league }, 'Лига наций', label, markets[key]);
        if (row) rows.push(row);
      }
      for (const [line, total] of Object.entries(markets.totals || {})) {
        addTotal(rows, fixture, { ...prediction, leagueLabel: fixture.league }, 'Лига наций', 'ТБ', line, total.over, total.under);
      }
    }
    return rows;
  }

  function euroFixtures(model, predictions, day, now) {
    const fixtures = new Map();
    if (!model) return [];
    for (const row of Array.isArray(predictions) ? predictions : []) {
      const time = Date.parse(row.time);
      if (!Number.isFinite(time) || time <= now || gameDayKeyMsk(time) !== day) continue;
      const competition = euroApi.resolveCompetition(model, row.league);
      if (!competition) continue;
      const home = euroApi.resolveTeam(model, row.home);
      const away = euroApi.resolveTeam(model, row.away);
      if (!competition || !home || !away) continue;
      const key = `${competition.code}|${home.key}|${away.key}`;
      if (!fixtures.has(key) || time < fixtures.get(key).time) {
        fixtures.set(key, { key, time, league: row.league, home: row.home, away: row.away, eventId: row.eventId || row.event_id || null });
      }
    }
    return [...fixtures.values()].sort((a, b) => a.time - b.time);
  }

  function euroCandidates(model, predictions, day, now) {
    const rows = [];
    for (const fixture of euroFixtures(model, predictions, day, now)) {
      const prediction = euroApi.predict(model, fixture.league, fixture.home, fixture.away);
      if (!prediction) continue;
      const resolved = { ...prediction, leagueLabel: prediction.competition?.name || fixture.league };
      const markets = prediction.markets || {};
      for (const [label, key] of [['П1', 'p1'], ['Х', 'x'], ['П2', 'p2'], ['1X', 'dc1x'], ['12', 'dc12'], ['X2', 'dcx2']]) {
        const row = candidate(fixture, resolved, 'Еврокубки', label, markets[key]);
        if (row) rows.push(row);
      }
      for (const [line, total] of Object.entries(markets.totals || {})) {
        addTotal(rows, fixture, resolved, 'Еврокубки', 'ТБ', line, total.over, total.under);
      }
      for (const [side, prefix] of [['home', 'ИТБ1'], ['away', 'ИТБ2']]) {
        for (const total of markets.teamGoals?.[side] || []) {
          addTotal(rows, fixture, resolved, 'Еврокубки', prefix, total.line, total.over, total.under);
        }
      }
    }
    return rows;
  }

  function buildCandidates(data, models, now = Date.now()) {
    const day = typeof data?.date === 'string' ? data.date : null;
    if (!day || !Array.isArray(data.predictions)) return [];
    const predictions = data.predictions;
    const rows = [
      ...goalsCandidates(models.goals, predictions, day, now),
      ...clubMarketCandidates(cornersApi, models.cornersCards, predictions, day, now, {
        corners: 'Угловые', yellowCards: 'ЖК',
      }),
      ...clubMarketCandidates(statsApi, models.stats, predictions, day, now, {
        fouls: 'Фолы', shots: 'Удары', shotsOnTarget: 'Удары в створ',
      }),
      ...nationsCandidates(models.nations, predictions, day, now),
      ...euroCandidates(models.euro, predictions, day, now),
      // Еврокубковые угловые/ЖК: та же форма prediction[market].lines, что у
      // клубной модели, поэтому используется общий перебор clubMarketCandidates.
      ...clubMarketCandidates(euroStatsApi, models.euroStats, predictions, day, now, {
        corners: 'Угловые', yellowCards: 'ЖК',
      }),
    ];
    return rows.sort((a, b) => (a.time - b.time) || (b.probability - a.probability) || a.label.localeCompare(b.label, 'ru'));
  }

  function candidateMarketNames(rows) {
    const present = new Set((Array.isArray(rows) ? rows : [])
      .map(row => String(row?.modelLabel || '').trim())
      .filter(Boolean));
    const known = CANDIDATE_MARKETS.filter(market => present.has(market));
    const additional = [...present].filter(market => !CANDIDATE_MARKETS.includes(market))
      .sort((a, b) => a.localeCompare(b, 'ru'));
    return [...known, ...additional];
  }

  function filterCandidatesByMarket(rows, market = 'all') {
    const candidates = Array.isArray(rows) ? rows : [];
    if (market === 'all') return [...candidates];
    return candidates.filter(row => row?.modelLabel === market);
  }

  function candidateBookmakerPrice(row, bookmaker) {
    const direct = row?.bookmakerPrices?.[bookmaker];
    if (Number.isFinite(direct?.odds) && direct.odds > 1) return direct;
    if (row?.bestBookmaker === bookmaker && Number.isFinite(row?.bestOdds) && row.bestOdds > 1) {
      return { bookmaker, odds: row.bestOdds };
    }
    return null;
  }

  function candidateBookmakerNames(rows) {
    const candidates = Array.isArray(rows) ? rows : [];
    if (!candidates.length) return [];
    const present = new Set();
    for (const row of candidates) {
      for (const [bookmaker, price] of Object.entries(row?.bookmakerPrices || {})) {
        if (Number.isFinite(price?.odds) && price.odds > 1) present.add(bookmaker);
      }
      if (row?.bestBookmaker && Number.isFinite(row?.bestOdds) && row.bestOdds > 1) {
        present.add(row.bestBookmaker);
      }
    }
    const additional = [...present].filter(bookmaker => !CANDIDATE_BOOKMAKERS.includes(bookmaker))
      .sort((a, b) => a.localeCompare(b, 'ru'));
    return [...CANDIDATE_BOOKMAKERS, ...additional];
  }

  function filterCandidatesByBookmaker(rows, bookmaker = 'all') {
    const candidates = Array.isArray(rows) ? rows : [];
    if (bookmaker === 'all') return [...candidates];
    return candidates.filter(row => candidateBookmakerPrice(row, bookmaker));
  }

  // Фильтр по сверке со Smart Tables: confirmed — направление совпало,
  // contradiction — противоположное, none — у ST нет направления по этому рынку,
  // marked — любой из двух первых. Направление берётся только из общих тоталов.
  function filterCandidatesBySt(rows, marks, selection = 'all') {
    const candidates = Array.isArray(rows) ? rows : [];
    if (!selection || selection === 'all') return [...candidates];
    return candidates.filter(row => {
      const status = stMarkerFor(marks, row?.home, row?.away, row?.modelLabel, row?.label)?.status || 'none';
      if (selection === 'marked') return status !== 'none';
      return status === selection;
    });
  }

  function groupCandidatesByMatch(rows) {
    const groups = new Map();
    for (const row of Array.isArray(rows) ? rows : []) {
      const key = [row.time, String(row.home || '').trim().toLowerCase(), String(row.away || '').trim().toLowerCase()].join('|');
      if (!groups.has(key)) {
        groups.set(key, {
          key,
          time: row.time,
          league: row.league,
          home: row.home,
          away: row.away,
          rows: [],
        });
      }
      groups.get(key).rows.push(row);
    }
    return [...groups.values()];
  }

  function groupCandidatesBySession(rows) {
    const groups = new Map(GAME_SESSIONS.map(session => [session.id, { ...session, rows: [] }]));
    for (const row of Array.isArray(rows) ? rows : []) {
      const session = gameSessionForTime(row.time);
      if (session) groups.get(session.id).rows.push(row);
    }
    return [...groups.values()].filter(group => group.rows.length > 0);
  }

  function finiteNumber(...values) {
    for (const value of values) {
      if (value === null || value === undefined || value === '') continue;
      const number = Number(value);
      if (Number.isFinite(number)) return number;
    }
    return null;
  }

  function candidatePriceKey(value) {
    const rawTime = value?.time;
    const time = rawTime instanceof Date ? rawTime.getTime()
      : (typeof rawTime === 'number' ? rawTime : Date.parse(rawTime));
    const modelKey = String(value?.modelKey || value?.model_key || '').trim();
    const model = String(value?.modelLabel || value?.model || '').trim();
    const label = String(value?.label || '').trim();
    if (!Number.isFinite(time) || !modelKey || !model || !label) return null;
    return JSON.stringify([modelKey, new Date(time).toISOString(), model, label]);
  }

  function mergeCandidatePrices(rows, payload) {
    const pricesByKey = new Map();
    for (const raw of Array.isArray(payload?.candidate_prices) ? payload.candidate_prices : []) {
      const key = candidatePriceKey(raw);
      const odds = finiteNumber(raw?.odds);
      const bookmaker = String(raw?.bookmaker || '').trim();
      if (!key || !Number.isFinite(odds) || odds <= 1 || !CANDIDATE_BOOKMAKERS.includes(bookmaker)) continue;
      if (!pricesByKey.has(key)) pricesByKey.set(key, { best: null, byBookmaker: {} });
      const entry = pricesByKey.get(key);
      const current = entry.byBookmaker[bookmaker];
      if (!current || odds > current.odds) entry.byBookmaker[bookmaker] = { odds, bookmaker };
      if (!entry.best || odds > entry.best.odds) entry.best = { odds, bookmaker };
    }
    return (Array.isArray(rows) ? rows : []).map(row => {
      const entry = pricesByKey.get(candidatePriceKey(row));
      return {
        ...row,
        bestOdds: entry?.best?.odds ?? null,
        bestBookmaker: entry?.best?.bookmaker ?? null,
        bookmakerPrices: entry?.byBookmaker || {},
      };
    });
  }

  // Текст счётчика претендентов без коэффициентов (summary.no_price файла купонов).
  // Главное число — «линия отсутствует у всех БК»: рынок сопоставлен,
  // но такой линии нет ни у одного букмекера (честное отсутствие, а не сбой сбора).
  function noPriceSummaryText(summary) {
    const noPrice = summary?.no_price;
    if (!noPrice || !Number.isFinite(noPrice.total) || noPrice.total <= 0) return null;
    const parts = [];
    if (noPrice.line_absent > 0) parts.push(`линия отсутствует у всех БК — ${noPrice.line_absent}`);
    if (noPrice.price_incomplete > 0) parts.push(`цена недоступна (односторонняя или целая линия) — ${noPrice.price_incomplete}`);
    if (noPrice.unmapped_market > 0) parts.push(`рынок вне сопоставления (индивидуальные тоталы) — ${noPrice.unmapped_market}`);
    if (noPrice.no_line_match > 0) parts.push(`матч не сопоставлен с линией — ${noPrice.no_line_match}`);
    return `Без коэффициентов: ${noPrice.total}${parts.length ? ` · ${parts.join(' · ')}` : ''}.`;
  }

  function normalizeLeg(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const fixture = raw.fixture && typeof raw.fixture === 'object' ? raw.fixture : {};
    const match = typeof raw.match === 'object' && raw.match ? raw.match : {};
    const home = raw.home || raw.home_team || fixture.home || match.home || '';
    const away = raw.away || raw.away_team || fixture.away || match.away || '';
    const label = raw.label || raw.market_label || raw.selection || raw.market_name ||
      (typeof raw.market === 'string' ? raw.market : '');
    if (!home || !away || !label) return null;
    const time = raw.time || raw.start_time || fixture.time || match.time || null;
    const session = gameSessionForTime(time);
    const declaredSession = raw.session_id || raw.sessionId || null;
    if (!session || (declaredSession && declaredSession !== session.id)) return null;
    return {
      time,
      eventId: raw.eventId || raw.event_id || fixture.eventId || fixture.event_id || match.eventId || match.event_id || null,
      modelKey: raw.modelKey || raw.model_key || null,
      model: raw.model || raw.modelLabel || null,
      league: raw.league || fixture.league || match.league || '—',
      home,
      away,
      label,
      sessionId: session.id,
      probability: finiteNumber(raw.p_model, raw.probability, raw.p),
      odds: finiteNumber(raw.odds, raw.decimal_odds, raw.price),
      fair: finiteNumber(raw.fair, raw.fair_market),
      deltaPp: finiteNumber(raw.delta_pp, raw.delta),
      ev: finiteNumber(raw.ev, raw.EV),
      bookmaker: raw.bookmaker || raw.bk || null,
    };
  }

  function normalizeCoupon(raw, index) {
    if (!raw || typeof raw !== 'object') return null;
    let sourceLegs = raw.legs || raw.selections || raw.items;
    if (!Array.isArray(sourceLegs)) sourceLegs = raw.leg ? [raw.leg] : [raw];
    const legs = sourceLegs.map(normalizeLeg);
    if (legs.some((leg) => !leg) || ![1, 2].includes(legs.length)) return null;
    const sessionIds = [...new Set(legs.map(leg => leg.sessionId).filter(Boolean))];
    const declaredSession = raw.session_id || raw.sessionId || null;
    if (sessionIds.length !== 1 || (declaredSession && declaredSession !== sessionIds[0])) return null;
    const bookmakers = [...new Set(legs.map(leg => leg.bookmaker || raw.bookmaker || raw.bk).filter(Boolean))];
    if (bookmakers.length !== 1) return null;
    const legOdds = legs.map(leg => leg.odds);
    const legProbabilities = legs.map(leg => leg.probability);
    const derivedOdds = legOdds.every((value) => Number.isFinite(value) && value > 1)
      ? legOdds.reduce((product, value) => product * value, 1)
      : null;
    const derivedProbability = legProbabilities.every((value) => Number.isFinite(value) && value >= 0 && value <= 1)
      ? legProbabilities.reduce((product, value) => product * value, 1)
      : null;
    const odds = finiteNumber(raw.odds, raw.combined_odds, raw.total_odds, raw.o_AB, derivedOdds);
    const probability = finiteNumber(raw.p_model, raw.probability, raw.P_AB, derivedProbability);
    const ev = finiteNumber(raw.ev, raw.EV, raw.EV_AB, raw.ev_AB,
      Number.isFinite(probability) && Number.isFinite(odds) ? probability * odds : null);
    if (!Number.isFinite(odds) || odds <= 1) return null;
    return {
      id: raw.coupon_id || raw.id || `coupon-${index + 1}`,
      type: legs.length === 2 ? 'double' : 'single',
      sessionId: sessionIds[0],
      bookmaker: bookmakers[0],
      legs,
      odds,
      probability,
      ev,
      status: 'prepared',
    };
  }

  function normalizeCoupons(payload) {
    const source = Array.isArray(payload?.prepared_coupons) ? payload.prepared_coupons : [];
    const rejected = [];
    const coupons = [];
    source.forEach((raw, index) => {
      const coupon = normalizeCoupon(raw, index);
      if (coupon) coupons.push(coupon);
      else rejected.push(index);
    });
    return { coupons, rejected };
  }

  function couponEmptyMessage(payload) {
    switch (payload?.data_status) {
      case 'not_processed': return 'Линии ещё не обработаны. Купоны появятся здесь после расчёта fair, EV и проверки цен.';
      case 'source_unavailable': return 'Источник линий недоступен. Купоны не формировались.';
      case 'no_line_match': return 'Линии обработаны, но модельные матчи не сопоставлены с событиями БК. Купонов нет.';
      case 'ok': return 'Линии обработаны: подходящих купонов нет. Сценарий дня — пропуск.';
      default: return 'Купоны ещё не опубликованы. Подготовленный купон не считается проставленным.';
    }
  }

  // --- Сверка со Smart Tables (data/st_marks_<date>.json, опционально) ---
  // Файл создаётся локальной сверкой в браузерной сессии владельца; в CI его нет.
  // Храним только факт подтверждения и направление — без данных Smart Tables.
  function stNormalizeName(name) {
    return String(name || '').toLowerCase()
      .normalize('NFD').replace(/[̀-ͯ]/g, '')
      .replace(/ø/g, 'o').replace(/æ/g, 'ae').replace(/ß/g, 'ss')
      .replace(/[^a-z0-9а-яё]+/gi, ' ')
      .replace(/\b(fc|fk|sk|cf|sc|ac|bk|if|ik|rc|ud|as|us|sv|afc|ssc|pae|club|racing|real|sporting|cp|sd|cd|ca|se|sl)\b/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function stMatchFor(marks, home, away) {
    if (!marks || !Array.isArray(marks.matches)) return null;
    const h = stNormalizeName(home);
    const a = stNormalizeName(away);
    if (!h || !a) return null;
    const same = (x, y) => x === y || (x.length >= 4 && y.includes(x)) || (y.length >= 4 && x.includes(y));
    return marks.matches.find(m => same(stNormalizeName(m.home), h) && same(stNormalizeName(m.away), a)) || null;
  }

  // direction кандидата: ТБ/ИТБ -> over, ТМ/ИТМ -> under
  function stDirectionOf(label) {
    if (/И?ТБ/.test(label)) return 'over';
    if (/И?ТМ/.test(label)) return 'under';
    return null;
  }

  // Возвращает null (нет данных по матчу/рынку) или { status: 'confirmed'|'contradiction', note }.
  function stMarkerFor(marks, home, away, marketLabel, label) {
    const match = stMatchFor(marks, home, away);
    if (!match) return null;
    const direction = stDirectionOf(label);
    if (!direction) return null;
    const mark = (match.marks || []).find(m => m.market === marketLabel && (m.direction === 'over' || m.direction === 'under'));
    if (!mark) return null;
    return { status: mark.direction === direction ? 'confirmed' : 'contradiction', note: mark.note || '' };
  }

  const api = {
    MOSCOW_OFFSET_MS,
    MIN_PROBABILITY,
    EV_MIN,
    GAME_SESSIONS,
    CANDIDATE_MARKETS,
    CANDIDATE_BOOKMAKERS,
    dayKeyMsk,
    gameDayKeyMsk,
    hasFutureFixtures,
    shouldRetainGameDaySnapshot,
    gameSessionForTime,
    minimumOdds,
    candidate,
    modelFixtures,
    nationsFixtures,
    euroFixtures,
    euroCandidates,
    buildCandidates,
    candidateMarketNames,
    filterCandidatesByMarket,
    candidateBookmakerPrice,
    candidateBookmakerNames,
    filterCandidatesByBookmaker,
    filterCandidatesBySt,
    groupCandidatesByMatch,
    groupCandidatesBySession,
    candidatePriceKey,
    mergeCandidatePrices,
    noPriceSummaryText,
    normalizeLeg,
    normalizeCoupon,
    normalizeCoupons,
    couponEmptyMessage,
    stNormalizeName,
    stMatchFor,
    stDirectionOf,
    stMarkerFor,
  };
  if (isNode) {
    module.exports = api;
    return;
  }
  root.DailyBoard = api;

  if (typeof document === 'undefined') return;
  const host = document.getElementById('daily-board');
  if (!host) return;

  const $ = (id) => document.getElementById(id);
  const pct = (value) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—';
  const decimal = (value) => Number.isFinite(value) ? value.toFixed(2) : '—';
  const timeMsk = (value) => {
    const time = typeof value === 'number' ? value : Date.parse(value);
    if (!Number.isFinite(time)) return 'Время не указано';
    return new Date(time).toLocaleString('ru-RU', {
      timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  };
  const dateRu = (day) => {
    const time = Date.parse(`${day}T00:00:00+03:00`);
    return Number.isFinite(time)
      ? new Date(time).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: 'long', year: 'numeric' })
      : day;
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function metric(label, value) {
    const item = node('span', 'daily-metric');
    item.append(node('span', 'daily-metric__label', label), node('strong', '', value));
    return item;
  }

  // Маркер сверки со Smart Tables: ✓ — их тренд в ту же сторону, ✗ — противоположный.
  let currentStMarks = null;
  // Сводка файла купонов дня (summary.no_price) — для счётчика претендентов без цены.
  let currentCouponSummary = null;

  function stBadge(home, away, marketLabel, label) {
    const result = stMarkerFor(currentStMarks, home, away, marketLabel, label);
    if (!result) return null;
    const confirmed = result.status === 'confirmed';
    const badge = node('span', `st-mark ${confirmed ? 'st-mark--confirmed' : 'st-mark--contradiction'}`,
      confirmed ? 'ST ✓' : 'ST ✗');
    badge.title = result.note
      || (confirmed ? 'Направление подтверждено статистикой Smart Tables' : 'Smart Tables: тренд противоположного направления — осторожно');
    return badge;
  }

  function marketCountLabel(value) {
    const mod100 = value % 100;
    const mod10 = value % 10;
    if (mod100 >= 11 && mod100 <= 14) return `${value} рынков`;
    if (mod10 === 1) return `${value} рынок`;
    if (mod10 >= 2 && mod10 <= 4) return `${value} рынка`;
    return `${value} рынков`;
  }

  function sessionHeader(session, countText) {
    const header = node('div', 'daily-session__header');
    const heading = node('div', 'daily-session__heading');
    heading.append(
      node('h4', '', `${session.label} сессия`),
      node('span', 'daily-session__time', `${session.timeLabel} МСК`),
    );
    header.append(heading, node('span', 'daily-session__count', countText));
    return header;
  }

  function candidateMatch(group, bookmaker = 'all') {
    const item = node('details', 'daily-match');
    const summary = node('summary', 'daily-match__summary');
    const toggle = node('span', 'daily-match__toggle');
    toggle.setAttribute('aria-hidden', 'true');
    const heading = node('span', 'daily-match__heading');
    const meta = node('span', 'daily-match__meta');
    meta.append(node('time', '', timeMsk(group.time)), node('span', '', group.league));
    heading.append(meta, node('strong', 'daily-match__teams', `${group.home} — ${group.away}`));
    summary.append(toggle, heading, node('span', 'daily-match__count', marketCountLabel(group.rows.length)));
    item.append(summary);

    const markets = node('div', 'daily-match__markets');
    for (const row of group.rows) {
      const marketRow = node('article', 'daily-row');
      const marketMeta = node('div', 'daily-row__meta');
      marketMeta.append(node('span', '', row.modelLabel));
      marketRow.append(marketMeta);
      const selectedPrice = bookmaker === 'all' ? null : candidateBookmakerPrice(row, bookmaker);
      const displayedOdds = selectedPrice?.odds ?? row.bestOdds;
      const displayedBookmaker = selectedPrice?.bookmaker ?? row.bestBookmaker;
      const market = node('div', 'daily-row__market');
      market.append(node('strong', '', row.label));
      const badge = stBadge(row.home, row.away, row.modelLabel, row.label);
      if (badge) market.append(badge);
      market.append(
        metric('Модель', pct(row.probability)),
        metric('Мин. кэф', decimal(row.oMinStep)),
        metric(bookmaker === 'all' ? 'Лучший кэф' : `Кэф ${bookmaker}`, Number.isFinite(displayedOdds)
          ? `${decimal(displayedOdds)} · ${displayedBookmaker}`
          : '—'),
      );
      marketRow.append(market);
      marketRow.append(node('p', 'daily-row__status', Number.isFinite(displayedOdds)
        ? `${bookmaker === 'all' ? 'Лучшая доступная цена трёх БК' : `Цена ${bookmaker}`} · справочно, не допуск в купон`
        : 'Цена отсутствует или рынок не подтверждён · fair и EV не рассчитаны'));
      markets.append(marketRow);
    }
    item.append(markets);
    return item;
  }

  let currentCandidateRows = [];
  let currentCandidateEmptyMessage = 'Загрузка претендентов…';
  let selectedCandidateMarket = 'all';
  let selectedCandidateBookmaker = 'all';
  let selectedStFilter = 'all';

  function appendCandidateFilterGroup(fragment, options) {
    const group = node('div', 'daily-market-filter-row');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', options.label);
    group.append(node('span', 'daily-market-filter-label', options.label));
    for (const value of ['all', ...options.values]) {
      const active = options.selected === value;
      const label = value === 'all' ? options.allLabel : (options.valueLabels?.[value] || value);
      const button = node('button', `daily-market-filter${active ? ' is-active' : ''}`, label);
      button.type = 'button';
      button.dataset[options.dataKey] = value;
      button.setAttribute('aria-pressed', String(active));
      button.addEventListener('click', () => {
        options.onSelect(value);
        renderCandidateFilters();
        renderCandidateList();
      });
      group.append(button);
    }
    fragment.append(group);
  }

  function renderCandidateFilters() {
    const bar = $('daily-candidate-filters');
    if (!bar) return;
    const markets = candidateMarketNames(currentCandidateRows);
    if (selectedCandidateMarket !== 'all' && !markets.includes(selectedCandidateMarket)) {
      markets.push(selectedCandidateMarket);
    }
    const bookmakers = candidateBookmakerNames(currentCandidateRows);
    if (selectedCandidateBookmaker !== 'all' && !bookmakers.includes(selectedCandidateBookmaker)) {
      bookmakers.push(selectedCandidateBookmaker);
    }
    const fragment = document.createDocumentFragment();
    if (markets.length) {
      appendCandidateFilterGroup(fragment, {
        label: 'Рынок', allLabel: 'Все рынки', values: markets,
        selected: selectedCandidateMarket, dataKey: 'market',
        onSelect: value => { selectedCandidateMarket = value; },
      });
    }
    if (bookmakers.length) {
      appendCandidateFilterGroup(fragment, {
        label: 'Букмекер', allLabel: 'Все БК', values: bookmakers,
        selected: selectedCandidateBookmaker, dataKey: 'bookmaker',
        onSelect: value => { selectedCandidateBookmaker = value; },
      });
    }
    const stAvailable = Array.isArray(currentStMarks?.matches) && currentStMarks.matches.length > 0;
    if (stAvailable) {
      appendCandidateFilterGroup(fragment, {
        label: 'Сверка ST', allLabel: 'Все', values: ['marked', 'confirmed', 'contradiction', 'none'],
        valueLabels: { marked: 'С отметкой ST', confirmed: 'ST ✓', contradiction: 'ST ✗', none: 'Без данных ST' },
        selected: selectedStFilter, dataKey: 'st',
        onSelect: value => { selectedStFilter = value; },
      });
    } else if (selectedStFilter !== 'all') {
      selectedStFilter = 'all';
    }
    bar.replaceChildren(fragment);
    bar.hidden = markets.length === 0 && bookmakers.length === 0 && !stAvailable;
  }

  function candidateFilterEmptyMessage() {
    if (selectedCandidateMarket !== 'all' && selectedCandidateBookmaker !== 'all') {
      return `По рынку «${selectedCandidateMarket}» и БК «${selectedCandidateBookmaker}» претендентов нет.`;
    }
    if (selectedCandidateBookmaker !== 'all') return `По БК «${selectedCandidateBookmaker}» претендентов нет.`;
    if (selectedCandidateMarket !== 'all') return `По рынку «${selectedCandidateMarket}» претендентов нет.`;
    if (selectedStFilter !== 'all') {
      const labels = { marked: 'с отметкой ST', confirmed: 'ST ✓', contradiction: 'ST ✗', none: 'без данных ST' };
      return `Претендентов ${labels[selectedStFilter] || ''} нет.`;
    }
    return currentCandidateEmptyMessage;
  }

  function renderStSummary() {
    const el = $('daily-st-summary');
    if (!el) return;
    if (!currentStMarks || !Array.isArray(currentStMarks.matches) || !currentStMarks.matches.length) {
      el.hidden = true;
      return;
    }
    let confirmed = 0;
    let contradictions = 0;
    for (const row of currentCandidateRows) {
      const result = stMarkerFor(currentStMarks, row.home, row.away, row.modelLabel, row.label);
      if (!result) continue;
      if (result.status === 'confirmed') confirmed++;
      else if (result.status === 'contradiction') contradictions++;
    }
    const checkedAt = currentStMarks.generatedAt ? timeMsk(currentStMarks.generatedAt) : null;
    el.textContent = `Сверка Smart Tables${checkedAt ? ` от ${checkedAt} МСК` : ''}: `
      + `матчей проверено ${currentStMarks.matches.length} · подтверждено направлений ${confirmed} · противоречий ${contradictions}. `
      + 'ST ✓ — тренд Smart Tables совпадает с направлением, ST ✗ — противоположный. Учитываются только общие тоталы матча.';
    el.hidden = false;
  }

  function renderNoPriceSummary() {
    const el = $('daily-noprice-summary');
    if (!el) return;
    const text = noPriceSummaryText(currentCouponSummary);
    el.textContent = text || '';
    el.hidden = !text;
  }

  function renderCandidateList() {
    const list = $('daily-candidates-list');
    const fragment = document.createDocumentFragment();
    const marketRows = filterCandidatesByMarket(currentCandidateRows, selectedCandidateMarket);
    const bookmakerRows = filterCandidatesByBookmaker(marketRows, selectedCandidateBookmaker);
    const rows = filterCandidatesBySt(bookmakerRows, currentStMarks, selectedStFilter);
    const groups = groupCandidatesBySession(rows);
    for (const session of groups) {
      const section = node('section', 'daily-session');
      section.append(sessionHeader(session, marketCountLabel(session.rows.length)));
      for (const group of groupCandidatesByMatch(session.rows)) {
        section.append(candidateMatch(group, selectedCandidateBookmaker));
      }
      fragment.append(section);
    }
    list.replaceChildren(fragment);
    const visibleCount = groups.reduce((total, session) => total + session.rows.length, 0);
    const empty = $('daily-candidates-empty');
    empty.textContent = currentCandidateRows.length > 0 && visibleCount === 0
      ? candidateFilterEmptyMessage()
      : currentCandidateEmptyMessage;
    $('daily-candidates-count').textContent = String(visibleCount);
    empty.hidden = visibleCount > 0;
    list.hidden = visibleCount === 0;
    renderStSummary();
    renderNoPriceSummary();
  }

  function renderCandidates(rows, emptyMessage) {
    currentCandidateRows = Array.isArray(rows) ? rows : [];
    if (typeof emptyMessage === 'string') currentCandidateEmptyMessage = emptyMessage;
    renderCandidateFilters();
    renderCandidateList();
  }

  function renderCouponLeg(leg) {
    const item = node('li', 'coupon-leg');
    const heading = node('div', 'coupon-leg__heading');
    heading.append(node('strong', '', `${leg.home} — ${leg.away}`), node('span', '', leg.label));
    const badge = stBadge(leg.home, leg.away, leg.model, leg.label);
    if (badge) heading.append(badge);
    item.append(heading);
    item.append(node('p', 'coupon-leg__meta', `${timeMsk(leg.time)} · ${leg.league}`));
    const metrics = node('div', 'daily-row__market');
    metrics.append(metric('Модель', pct(leg.probability)), metric('Кэф', decimal(leg.odds)), metric('EV', decimal(leg.ev)));
    item.append(metrics);
    return item;
  }

  let currentCouponPayload = { data_status: 'not_processed', prepared_coupons: [] };
  let currentCouponDay = dayKeyMsk(Date.now());

  function couponCard(coupon, day, confirmationApi, options = {}) {
    const placed = confirmationApi?.getPlaced ? confirmationApi.getPlaced(day, coupon) : null;
    const shown = placed?.coupon || coupon;
    const item = node('article', `coupon coupon--${shown.type}`);
    const header = node('div', 'coupon__header');
    const status = node('span', 'coupon__status', 'Подготовлен · не проставлен');
    const title = shown.type === 'double' ? 'Двойник' : 'Одинар';
    header.append(
      node('h4', '', options.dayLabel ? `${title} · ${options.dayLabel}` : title),
      status,
    );
    item.append(header);
    const metrics = node('div', 'coupon__metrics');
    metrics.append(
      metric('БК', shown.bookmaker),
      metric('Итоговый кэф', decimal(shown.odds)),
      metric('Модель', pct(shown.probability)),
      metric('EV', decimal(shown.ev)),
    );
    item.append(metrics);
    const legs = node(shown.type === 'double' ? 'ol' : 'ul', 'coupon-legs');
    for (const leg of shown.legs) legs.append(renderCouponLeg(leg));
    item.append(legs);
    if (confirmationApi) {
      item.append(confirmationApi.mountCoupon({
        coupon, day, statusElement: status, onChange: options.onChange,
      }));
    }
    return item;
  }

  function sessionCouponCount(rows) {
    const singles = rows.filter(coupon => coupon.type === 'single').length;
    const doubles = rows.filter(coupon => coupon.type === 'double').length;
    const singleLabel = singles === 1 ? 'одинар' : 'одинара';
    const doubleLabel = doubles === 1 ? 'двойник' : 'двойника';
    return `${singles} ${singleLabel} · ${doubles} ${doubleLabel}`;
  }

  function renderCoupons(payload, day) {
    currentCouponPayload = payload;
    currentCouponDay = day;
    const { coupons, rejected } = normalizeCoupons(payload);
    const list = $('daily-coupons-list');
    const fragment = document.createDocumentFragment();
    const confirmationApi = host.dataset.confirmations === 'true' ? root.CouponConfirmation : null;
    for (const session of GAME_SESSIONS) {
      const sessionCoupons = coupons.filter(coupon => coupon.sessionId === session.id);
      if (!sessionCoupons.length) continue;
      const section = node('section', 'daily-session');
      section.append(sessionHeader(session, sessionCouponCount(sessionCoupons)));
      for (const coupon of sessionCoupons) {
        section.append(couponCard(coupon, day, confirmationApi, { onChange: refreshPlacementViews }));
      }
      fragment.append(section);
    }
    list.replaceChildren(fragment);
    $('daily-coupons-count').textContent = String(coupons.length);
    const empty = $('daily-coupons-empty');
    empty.textContent = couponEmptyMessage(payload);
    empty.hidden = coupons.length > 0;
    list.hidden = coupons.length === 0;
    $('daily-coupons-warning').hidden = rejected.length === 0;
    if (rejected.length) {
      $('daily-coupons-warning').textContent = `Не показано записей с неполной схемой, разными сессиями или разными БК у двойника: ${rejected.length}.`;
    }
  }

  function renderPlacementHistory() {
    const list = $('placed-bets-list');
    if (!list) return;
    const confirmationApi = host.dataset.confirmations === 'true' ? root.CouponConfirmation : null;
    const records = confirmationApi?.listPlaced ? confirmationApi.listPlaced() : [];
    const fragment = document.createDocumentFragment();
    for (const record of records) {
      fragment.append(couponCard(record.coupon, record.day, confirmationApi, {
        dayLabel: dateRu(record.day), onChange: refreshPlacementViews,
      }));
    }
    list.replaceChildren(fragment);
    list.hidden = records.length === 0;
    $('placed-bets-count').textContent = String(records.length);
    $('placed-bets-empty').hidden = records.length > 0;
  }

  function refreshPlacementViews() {
    renderCoupons(currentCouponPayload, currentCouponDay);
    renderPlacementHistory();
  }

  function renderSessionSchedule() {
    const schedule = $('daily-session-schedule');
    if (!schedule) return;
    const fragment = document.createDocumentFragment();
    for (const session of GAME_SESSIONS) {
      const item = node('div', 'session-schedule__item');
      item.append(
        node('strong', '', session.label),
        node('span', '', `${session.timeLabel} МСК`),
      );
      fragment.append(item);
    }
    schedule.replaceChildren(fragment);
  }

  async function fetchJson(path) {
    const response = await fetch(`${path}${path.includes('?') ? '&' : '?'}t=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  }

  async function load() {
    $('daily-board-status').textContent = 'Загрузка модельного снимка…';
    $('daily-board-status').className = 'daily-board__status is-loading';
    try {
      const [data, goals, cornersCards, stats, nations, euro, euroStats] = await Promise.all([
        fetchJson('./data.json'),
        fetchJson('./data/goals_markets_models.json'),
        fetchJson('./data/corners_cards_models.json'),
        fetchJson('./data/stats_markets_models.json'),
        fetchJson('./data/nt_model.json'),
        fetchJson('./data/euro_cups_model.json'),
        // Файл появляется после первого переобучения с евро-статистикой;
        // до этого доска работает без него.
        fetchJson('./data/euro_stats_models.json').catch(() => null),
      ]);
      const now = Date.now();
      const currentGameDay = gameDayKeyMsk(now);
      const isCurrent = data.date === currentGameDay;
      // Маркеры сверки со Smart Tables — опциональный файл дня; без него доска работает как раньше.
      currentStMarks = await fetchJson(`./data/st_marks_${data.date}.json`).catch(() => null);
      currentCouponSummary = null;
      const candidates = isCurrent
        ? buildCandidates(data, { goals, cornersCards, stats, nations, euro, euroStats }, now)
        : [];
      renderCandidates(candidates, isCurrent
        ? 'На оставшуюся часть игрового дня нет модельных рынков с вероятностью от 75%.'
        : `Снимок относится к ${dateRu(data.date)}, а не к текущему московскому дню. Данные нужно обновить.`);
      $('daily-board-date').textContent = `${dateRu(data.date)} · время МСК`;

      if (!isCurrent) {
        renderCoupons({ data_status: 'stale_snapshot', prepared_coupons: [] });
        $('daily-coupons-empty').textContent = 'Купоны скрыты: дата модельного снимка не совпадает с текущим игровым днём.';
      } else {
        try {
          const couponPayload = await fetchJson(`./analysis/doubles_${data.date}.json`);
          if (couponPayload.date && couponPayload.date !== data.date) throw new Error('Дата файла купонов не совпадает со снимком');
          currentCouponSummary = couponPayload?.summary || null;
          renderCandidates(mergeCandidatePrices(candidates, couponPayload));
          renderCoupons(couponPayload, data.date);
        } catch (error) {
          renderCoupons({ data_status: 'not_processed', prepared_coupons: [] });
          $('daily-coupons-empty').textContent = error.message.includes('HTTP 404')
            ? couponEmptyMessage({ data_status: 'not_processed' })
            : `Не удалось загрузить купоны: ${error.message}`;
        }
      }
      $('daily-board-status').textContent = isCurrent ? 'Модельный снимок загружен' : 'Снимок устарел';
      $('daily-board-status').className = `daily-board__status ${isCurrent ? 'is-ready' : 'is-warning'}`;
    } catch (error) {
      renderCandidates([], 'Не удалось загрузить активные модели или календарь.');
      renderCoupons({ data_status: 'source_unavailable', prepared_coupons: [] });
      $('daily-board-status').textContent = 'Ошибка загрузки';
      $('daily-board-status').className = 'daily-board__status is-warning';
      $('daily-board-date').textContent = error.message;
    }
  }

  const refresh = $('daily-board-refresh');
  if (refresh) {
    refresh.addEventListener('click', async () => {
      refresh.disabled = true;
      refresh.textContent = 'Обновление…';
      try {
        await load();
      } finally {
        refresh.disabled = false;
        refresh.textContent = 'Обновить данные';
      }
    });
  }

  renderSessionSchedule();
  renderPlacementHistory();
  load();
}(typeof self !== 'undefined' ? self : this));
