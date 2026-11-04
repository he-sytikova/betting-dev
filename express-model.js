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
  // probabilities are non-negative, so for any pair of matches the best
  // combination is the product of their best legs — and each unordered pair of
  // matches appears at most once.
  function selectExpresses(legs, options = {}) {
    const minP = options.minLegProbability ?? MIN_LEG_PROBABILITY;
    const limit = options.limit ?? EXPRESS_LIMIT;
    const bestByMatch = new Map();
    for (const leg of legs) {
      if (!Number.isFinite(leg.probability) || leg.probability < minP) continue;
      const current = bestByMatch.get(leg.matchKey);
      if (!current || leg.probability > current.probability) bestByMatch.set(leg.matchKey, leg);
    }
    const best = [...bestByMatch.values()];
    const expresses = [];
    for (let i = 0; i < best.length; i++) {
      for (let j = i + 1; j < best.length; j++) {
        const a = best[i], b = best[j];
        const joint = a.probability * b.probability;
        expresses.push({ legs: [a, b], probability: joint, fairOdds: 1 / joint });
      }
    }
    return expresses
      .sort((x, y) => y.probability - x.probability || x.legs[0].time - y.legs[0].time)
      .slice(0, limit);
  }

  const api = { dayKeyMsk, extractLegs, buildDailyLegs, selectExpresses, MIN_LEG_PROBABILITY, EXPRESS_LIMIT };
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

  function renderExpresses(expresses) {
    host.replaceChildren();
    for (const [index, exp] of expresses.entries()) {
      const tr = document.createElement('tr');
      cell(tr, String(index + 1));
      const [a, b] = exp.legs;
      cell(tr, fmtTime(a.time) + ' · ' + a.league + '\n' + a.home + ' — ' + a.away + '\n' + a.label + ' (' + pct(a.probability) + ')');
      cell(tr, fmtTime(b.time) + ' · ' + b.league + '\n' + b.home + ' — ' + b.away + '\n' + b.label + ' (' + pct(b.probability) + ')');
      const joint = probCell(tr, exp.probability);
      joint.className = 'express-joint';
      joint.style.backgroundColor = heat(exp.probability);
      joint.style.color = '#000';
      joint.title = 'Совместная вероятность: ' + pct(exp.probability);
      cell(tr, exp.fairOdds.toFixed(2));
      host.append(tr);
    }
    $('express-empty').hidden = expresses.length > 0;
  }

  function renderLegs(legs) {
    const legsHost = $('express-legs-rows');
    legsHost.replaceChildren();
    const eligible = legs
      .filter(l => Number.isFinite(l.probability) && l.probability >= MIN_LEG_PROBABILITY)
      .sort((x, y) => y.probability - x.probability || x.time - y.time);
    for (const leg of eligible) {
      const tr = document.createElement('tr');
      cell(tr, fmtTime(leg.time));
      cell(tr, leg.league);
      cell(tr, leg.home + ' — ' + leg.away);
      cell(tr, leg.label);
      probCell(tr, leg.probability);
      legsHost.append(tr);
    }
    $('express-legs-empty').hidden = eligible.length > 0;
    return eligible.length;
  }

  async function load() {
    $('express-status').textContent = 'Загрузка внутренней модели…';
    try {
      const [modelsResponse, dataResponse] = await Promise.all([
        fetch('./data/goals_markets_models.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data.json?t=' + Date.now(), { cache: 'no-store' }),
      ]);
      if (!modelsResponse.ok) throw Error('goals_markets_models.json: HTTP ' + modelsResponse.status);
      if (!dataResponse.ok) throw Error('data.json: HTTP ' + dataResponse.status);
      const model = await modelsResponse.json();
      const data = await dataResponse.json();
      const legs = buildDailyLegs(model, data.predictions, data.date || null);
      const expresses = selectExpresses(legs);
      renderExpresses(expresses);
      const eligibleCount = renderLegs(legs);
      $('express-count').textContent = 'Дата снимка: ' + (data.date || '—') + ' (МСК). Событий с вероятностью от ' +
        Math.round(MIN_LEG_PROBABILITY * 100) + '%: ' + eligibleCount + '. Модель обучена по ' + (model.trainingThrough || '—') + '.';
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
