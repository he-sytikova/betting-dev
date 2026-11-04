(() => {
  'use strict';

  // Daily accumulator ("express") selector on top of the internal goals model.
  // From today's fixtures it takes only the safest markets — double chance
  // (1X/12/X2) and goal totals (over/under every exported line) — and builds the
  // top-5 two-leg accumulators by joint model probability. Probabilities are
  // never invented: a leg exists only when the goals model can score the match.

  const goalsApi = (typeof module !== 'undefined' && module.exports)
    ? require('./goals-internal-model.js')
    : (typeof window !== 'undefined' ? window.GoalsInternalModel : null);

  const MOSCOW_OFFSET_MIN = 180; // Europe/Moscow has no DST since 2014.
  const MIN_LEG_PROBABILITY = 0.60;
  const EXPRESS_LIMIT = 5;
  // Value mode (active only when bookmaker odds are attached): a leg must beat
  // its break-even price (p * odds >= 1) and the combined bookmaker price of a
  // pair stays capped so the accumulator cannot drift into longshot territory.
  const MIN_LEG_EV = 1.0;
  const MAX_COMBINED_ODDS = 4.0;

  function dayKeyMsk(timeMs) {
    const d = new Date(timeMs + MOSCOW_OFFSET_MIN * 60000);
    return d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0') + '-' + String(d.getUTCDate()).padStart(2, '0');
  }

  // Allowed markets only: double chance + totals. Straight 1X2 outcomes are
  // deliberately excluded as too risky for high-passability accumulators.
  function extractLegs(model, fixture) {
    const prediction = goalsApi.predict(model, fixture.league, fixture.home, fixture.away);
    if (!prediction) return [];
    const matchKey = goalsApi.canonicalFixtureKey(model, fixture.league, fixture.home, fixture.away);
    const base = { matchKey, time: fixture.time, league: fixture.league, home: fixture.home, away: fixture.away };
    const legs = [];
    const dc = prediction.outcomes;
    for (const [label, value] of [['1X', dc.dc1x], ['12', dc.dc12], ['X2', dc.dcx2]]) {
      if (Number.isFinite(value)) legs.push({ ...base, market: 'doubleChance', label, probability: value });
    }
    for (const t of prediction.totalGoals || []) {
      if (Number.isFinite(t.over)) legs.push({ ...base, market: 'total', label: 'ТБ ' + t.line, probability: t.over });
      if (Number.isFinite(t.under)) legs.push({ ...base, market: 'total', label: 'ТМ ' + t.line, probability: t.under });
    }
    return legs;
  }

  function buildDailyLegs(model, predictions, dayKey) {
    const fixtures = goalsApi.uniqueFixtures(predictions, model)
      .filter(f => !dayKey || dayKeyMsk(f.time) === dayKey);
    const legs = [];
    for (const fixture of fixtures) legs.push(...extractLegs(model, fixture));
    return legs;
  }

  // Top-N two-leg accumulators: legs always come from different matches, the
  // joint probability assumes independence (different matches, no correlation
  // adjustment). Only the single strongest eligible leg of each match competes —
  // and each unordered pair of matches appears at most once.
  // Two selection modes:
  //  - probability mode (fallback, no odds attached): strongest = highest model
  //    probability, pairs are ranked by joint probability;
  //  - value mode (odds attached for at least one leg): a leg is eligible only
  //    with EV = p * odds >= minEv, strongest = highest EV, pairs are ranked by
  //    joint EV and dropped when the combined bookmaker price exceeds the cap.
  function selectExpresses(legs, options = {}) {
    const minP = options.minLegProbability ?? MIN_LEG_PROBABILITY;
    const limit = options.limit ?? EXPRESS_LIMIT;
    const minEv = options.minLegEv ?? MIN_LEG_EV;
    const maxCombinedOdds = options.maxCombinedOdds ?? MAX_COMBINED_ODDS;
    const valueMode = legs.some(l => Number.isFinite(l.odds));
    const scoreOf = leg => valueMode ? leg.ev : leg.probability;
    const bestByMatch = new Map();
    for (const leg of legs) {
      if (!Number.isFinite(leg.probability) || leg.probability < minP) continue;
      if (valueMode && (!Number.isFinite(leg.odds) || !(leg.ev >= minEv))) continue;
      const current = bestByMatch.get(leg.matchKey);
      if (!current || scoreOf(leg) > scoreOf(current)) bestByMatch.set(leg.matchKey, leg);
    }
    const best = [...bestByMatch.values()];
    const expresses = [];
    for (let i = 0; i < best.length; i++) {
      for (let j = i + 1; j < best.length; j++) {
        const a = best[i], b = best[j];
        const joint = a.probability * b.probability;
        const combinedOdds = valueMode ? a.odds * b.odds : null;
        if (combinedOdds !== null && combinedOdds > maxCombinedOdds) continue;
        expresses.push({
          legs: [a, b], probability: joint, fairOdds: 1 / joint,
          combinedOdds, ev: valueMode ? joint * combinedOdds : null,
        });
      }
    }
    const byJointValue = (x, y) => (y.ev - x.ev) || (y.probability - x.probability) || (x.legs[0].time - y.legs[0].time);
    const byJointProbability = (x, y) => (y.probability - x.probability) || (x.legs[0].time - y.legs[0].time);
    return expresses.sort(valueMode ? byJointValue : byJointProbability).slice(0, limit);
  }

  // Index: model fixture key -> Euro365 event id, from the Euro365 rows of the
  // production snapshot (other providers have their own id spaces and never
  // contribute). Lets express legs find their bookmaker odds.
  function buildEventIndex(model, predictions) {
    const map = new Map();
    for (const p of predictions || []) {
      if (p.source !== 'Euro365' || p.eventId === undefined || p.eventId === null) continue;
      const key = goalsApi.canonicalFixtureKey(model, p.league, p.home, p.away);
      if (!map.has(key)) map.set(key, String(p.eventId));
    }
    return map;
  }

  const TOTAL_LABEL = /^Т([БМ]) (\d+(?:\.\d+)?)$/;

  // Raw decimal price for one leg from the event's Euro365 markets; null when
  // the market or line is missing (no synthesized prices).
  function legOdds(leg, markets) {
    if (!markets) return null;
    let odds = null;
    if (leg.market === 'doubleChance' && markets.doubleChance) {
      odds = markets.doubleChance[leg.label];
    } else if (leg.market === 'total' && markets.totals) {
      const match = TOTAL_LABEL.exec(leg.label);
      const line = match ? markets.totals[match[2]] : null;
      if (line) odds = match[1] === 'Б' ? line.over : line.under;
    }
    return Number.isFinite(odds) && odds > 1 ? odds : null;
  }

  // Attach bookmaker odds (and EV = p * odds) to legs in place; legs without a
  // matching event or market keep no odds and stay probability-only.
  function attachOdds(legs, oddsData, eventIndex) {
    const events = (oddsData && oddsData.events) || {};
    for (const leg of legs) {
      const eventId = eventIndex ? eventIndex.get(leg.matchKey) : null;
      const event = eventId != null ? events[String(eventId)] : null;
      const odds = event ? legOdds(leg, event.markets) : null;
      if (odds !== null) {
        leg.odds = odds;
        leg.ev = leg.probability * odds;
      }
    }
    return legs;
  }

  const api = { dayKeyMsk, extractLegs, buildDailyLegs, selectExpresses, buildEventIndex, attachOdds, legOdds, MIN_LEG_PROBABILITY, EXPRESS_LIMIT, MIN_LEG_EV, MAX_COMBINED_ODDS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  // ---- Browser rendering on express-model.html ----
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  window.ExpressModel = api;
  const host = document.getElementById('express-rows');
  if (!host) return;

  const $ = id => document.getElementById(id);
  const pct = n => Number.isFinite(n) ? (n * 100).toFixed(1) + '%' : '—';
  // Единый с другими страницами градиент: чем выше вероятность, тем насыщеннее зелёный.
  const heat = p => { const x = Math.max(0, Math.min(1, p)), a = [240, 253, 244], b = [20, 83, 45]; return 'rgb(' + a.map((v, i) => Math.round(v + (b[i] - v) * x)).join(', ') + ')'; };
  const fmtTime = t => new Date(t).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  function cell(tr, value) { const td = document.createElement('td'); td.textContent = value; tr.append(td); return td; }
  function probCell(tr, p) {
    const td = cell(tr, pct(p));
    if (Number.isFinite(p)) {
      td.style.backgroundColor = heat(p);
      td.style.color = '#000';
      td.title = 'Вероятность: ' + pct(p);
      if (p > .65) td.style.fontWeight = '650';
    }
    return td;
  }

  const fmtOdds = o => Number.isFinite(o) ? o.toFixed(2) : '—';
  const fmtEv = ev => Number.isFinite(ev) ? ev.toFixed(2) : '—';
  const legText = leg => fmtTime(leg.time) + ' · ' + leg.league + '\n' + leg.home + ' — ' + leg.away + '\n' +
    leg.label + ' (' + pct(leg.probability) + (Number.isFinite(leg.odds) ? ' × ' + fmtOdds(leg.odds) : '') + ')';

  function renderExpresses(expresses) {
    host.replaceChildren();
    for (const [index, exp] of expresses.entries()) {
      const tr = document.createElement('tr');
      cell(tr, String(index + 1));
      const [a, b] = exp.legs;
      cell(tr, legText(a));
      cell(tr, legText(b));
      const joint = probCell(tr, exp.probability);
      joint.className = 'express-joint';
      joint.style.backgroundColor = heat(exp.probability);
      joint.style.color = '#000';
      joint.title = 'Совместная вероятность: ' + pct(exp.probability);
      cell(tr, exp.fairOdds.toFixed(2));
      cell(tr, fmtOdds(exp.combinedOdds));
      const evTd = cell(tr, fmtEv(exp.ev));
      if (Number.isFinite(exp.ev)) evTd.title = 'EV = совместная вероятность × суммарный кэф; выше 1.00 — модель оценивает событие выше букмекера';
      host.append(tr);
    }
    $('express-empty').hidden = expresses.length > 0;
  }

  function renderLegs(legs) {
    const legsHost = $('express-legs-rows');
    legsHost.replaceChildren();
    const valueMode = legs.some(l => Number.isFinite(l.odds));
    const eligible = legs
      .filter(l => Number.isFinite(l.probability) && l.probability >= MIN_LEG_PROBABILITY)
      .sort((x, y) => (valueMode ? (y.ev || 0) - (x.ev || 0) : y.probability - x.probability) || x.time - y.time);
    for (const leg of eligible) {
      const tr = document.createElement('tr');
      cell(tr, fmtTime(leg.time));
      cell(tr, leg.league);
      cell(tr, leg.home + ' — ' + leg.away);
      cell(tr, leg.label);
      probCell(tr, leg.probability);
      cell(tr, fmtOdds(leg.odds));
      const evTd = cell(tr, fmtEv(leg.ev));
      if (Number.isFinite(leg.ev) && leg.ev >= MIN_LEG_EV) { evTd.style.fontWeight = '650'; }
      legsHost.append(tr);
    }
    $('express-legs-empty').hidden = eligible.length > 0;
    return eligible.length;
  }

  async function load() {
    $('express-status').textContent = 'Загрузка внутренней модели…';
    try {
      const [modelsResponse, dataResponse, oddsResponse] = await Promise.all([
        fetch('./data/goals_markets_models.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data/odds.json?t=' + Date.now(), { cache: 'no-store' }),
      ]);
      if (!modelsResponse.ok) throw Error('goals_markets_models.json: HTTP ' + modelsResponse.status);
      if (!dataResponse.ok) throw Error('data.json: HTTP ' + dataResponse.status);
      const model = await modelsResponse.json();
      const data = await dataResponse.json();
      // Odds are optional and must belong to the same snapshot day, otherwise
      // yesterday's prices would silently attach to today's fixtures.
      let oddsData = null;
      if (oddsResponse.ok) {
        const candidate = await oddsResponse.json();
        if (candidate && candidate.date === (data.date || null) && candidate.events) oddsData = candidate;
      }
      const legs = buildDailyLegs(model, data.predictions, data.date || null);
      const eventIndex = buildEventIndex(model, data.predictions);
      attachOdds(legs, oddsData, eventIndex);
      const valueMode = legs.some(l => Number.isFinite(l.odds));
      const expresses = selectExpresses(legs);
      renderExpresses(expresses);
      const eligibleCount = renderLegs(legs);
      $('express-count').textContent = 'Дата снимка: ' + (data.date || '—') + ' (МСК). Событий с вероятностью от ' +
        Math.round(MIN_LEG_PROBABILITY * 100) + '%: ' + eligibleCount + '. Модель обучена по ' + (model.trainingThrough || '—') + '. ' +
        (valueMode
          ? 'Кэфы Euro365 от ' + (oddsData.generatedAt || '—').slice(0, 16).replace('T', ' ') + ' UTC; отбор по value (EV = p × кэф ≥ ' + MIN_LEG_EV.toFixed(2) + '), суммарный кэф до ' + MAX_COMBINED_ODDS + '.'
          : 'Кэфы Euro365 недоступны — отбор по максимальной вероятности (без value-фильтра).');
      $('express-status').textContent = 'Внутренняя модель загружена (снимок от ' + (model.trainingThrough || '—') + ').';
    } catch (e) {
      $('express-status').textContent = 'Внутренняя модель недоступна: ' + e.message;
      $('express-empty').hidden = false;
      $('express-legs-empty').hidden = false;
    }
  }
  const refreshButton = $('refresh');
  if (refreshButton) refreshButton.addEventListener('click', load);
  load();
})();
