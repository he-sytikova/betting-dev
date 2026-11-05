/* Блок «Кэфы дня» на goals-model.html.
 *
 * Справочная таблица цен источников для матчей текущего снимка, которые
 * анализируют наши модели (клубные лиги модели голов + еврокубки). Цены
 * НИКАК не участвуют в расчёте вероятностей — только отображение:
 *   Euro365      — текущая интрадей-цена одного букмекера (1X2, ДШ, тоталы);
 *   The Odds API — Avg/Max по европейским букмекерам (только тоталы,
 *                  только клубные лиги дня);
 *   FDO          — консенсус открытия рынка football-data.co.uk (только
 *                  клубные лиги, справочно — цены могли уйти).
 * Матч без единой цены не выводится; вероятности не выдумываются.
 */
(() => {
  'use strict';

  const goalsApi = (typeof module !== 'undefined' && module.exports)
    ? require('./goals-internal-model.js')
    : (typeof window !== 'undefined' ? window.GoalsInternalModel : null);
  const euroApi = (typeof module !== 'undefined' && module.exports)
    ? require('./euro-internal-model.js')
    : (typeof window !== 'undefined' ? window.EuroInternalModel || null : null);
  const expressApi = (typeof module !== 'undefined' && module.exports)
    ? require('./express-model.js')
    : (typeof window !== 'undefined' ? window.ExpressModel || null : null);

  // Основные линии тоталов для отображения; азиатские четвертные (2.75, 3.25)
  // и целые (2.0, 3.0) опускаем — модель такие линии не оценивает, а таблица
  // иначе раздувается.
  const DISPLAY_TOTAL_LINES = ['1.5', '2.5', '3.5'];

  function fmtPrice(v) {
    return Number.isFinite(v) && v > 1 ? v.toFixed(2) : null;
  }

  // Компактное представление рынков Euro365 одного события; null, если рынков
  // нет вовсе.
  function pickEuro365View(markets) {
    if (!markets) return null;
    const view = {};
    const m1x2 = markets['1x2'];
    if (m1x2) {
      const p1 = fmtPrice(m1x2['1']), x = fmtPrice(m1x2.X), p2 = fmtPrice(m1x2['2']);
      if (p1 && x && p2) view.o1x2 = { p1, x, p2 };
    }
    const dc = markets.doubleChance;
    if (dc) {
      const d1x = fmtPrice(dc['1X']), d12 = fmtPrice(dc['12']), dx2 = fmtPrice(dc.X2);
      if (d1x && d12 && dx2) view.dc = { '1X': d1x, '12': d12, 'X2': dx2 };
    }
    const totals = {};
    for (const line of DISPLAY_TOTAL_LINES) {
      const t = (markets.totals || {})[line];
      if (!t) continue;
      const over = fmtPrice(t.over), under = fmtPrice(t.under);
      if (over && under) totals[line] = { over, under };
    }
    if (Object.keys(totals).length) view.totals = totals;
    return Object.keys(view).length ? view : null;
  }

  // The Odds API: только тоталы, показываем Max (лучшая текущая цена среди
  // европейских букмекеров); Avg уезжает в title-подсказку при рендере.
  function pickToaView(markets) {
    if (!markets) return null;
    const totals = {};
    for (const line of DISPLAY_TOTAL_LINES) {
      const t = (markets.totals || {})[line];
      if (!t) continue;
      const over = fmtPrice(t.max ? t.max.over : null), under = fmtPrice(t.max ? t.max.under : null);
      if (!over || !under) continue;
      totals[line] = {
        over, under,
        avgOver: fmtPrice(t.avg ? t.avg.over : null),
        avgUnder: fmtPrice(t.avg ? t.avg.under : null),
      };
    }
    return Object.keys(totals).length ? { totals } : null;
  }

  // FDO: консенсус открытия (avg), 1X2 и тоталы. Снимок после открытия рынка —
  // цены справочные, текущей доступности не гарантируют.
  function pickFdoView(markets) {
    if (!markets) return null;
    const view = {};
    const m1x2 = markets['1x2'] && markets['1x2'].avg;
    if (m1x2) {
      const p1 = fmtPrice(m1x2['1']), x = fmtPrice(m1x2.X), p2 = fmtPrice(m1x2['2']);
      if (p1 && x && p2) view.o1x2 = { p1, x, p2 };
    }
    const totals = {};
    for (const line of DISPLAY_TOTAL_LINES) {
      const t = (markets.totals || {})[line];
      const avg = t && t.avg;
      if (!avg) continue;
      const over = fmtPrice(avg.over), under = fmtPrice(avg.under);
      if (over && under) totals[line] = { over, under };
    }
    if (Object.keys(totals).length) view.totals = totals;
    return Object.keys(view).length ? view : null;
  }

  // Тот же дневной гейт, что у экспрессов: артефакт кэфов обязан относиться
  // к дате снимка, иначе вчерашние цены приклеятся к сегодняшним матчам.
  function sameDayGate(artifact, snapshotDate) {
    return Boolean(artifact && artifact.date && snapshotDate && artifact.date === snapshotDate);
  }

  // Собирает строки блока. fixtures — предварительно отобранные матчи
  // {time, league, home, away, kind, matchKey}; индексы — из express-model.js
  // (buildEventIndex/buildFdoIndex/buildOddsApiIndex, уже покрыты тестами).
  // Матч без единой цены в строки не попадает.
  function buildRows(fixtures, { events, eventIndex, fdoIndex, oddsApiIndex }) {
    const rows = [];
    for (const f of fixtures) {
      const eventId = eventIndex ? eventIndex.get(f.matchKey) : null;
      const event = eventId != null && events ? events[String(eventId)] : null;
      const euro365 = pickEuro365View(event ? event.markets : null);
      const fdo = pickFdoView(fdoIndex ? fdoIndex.get(f.matchKey) : null);
      const toa = pickToaView(oddsApiIndex ? oddsApiIndex.get(f.matchKey) : null);
      if (!euro365 && !fdo && !toa) continue;
      rows.push({
        time: f.time, league: f.league, match: f.home + ' — ' + f.away,
        kind: f.kind, euro365, fdo, toa,
      });
    }
    return rows.sort((a, b) => a.time - b.time);
  }

  // Матчи снимка, которые анализируют наши модели: клубные лиги модели голов
  // (kind 'club') или еврокубки (kind 'euro'). Дедупликация — как на основной
  // таблице: canonicalFixtureKey по модели голов (для еврокубковых матчей
  // срабатывает raw-ключ по нормализованным именам — он же используется при
  // построении eventIndex, поэтому связка с кэфами Euro365 сохраняется).
  function analyzableFixtures(goalsModel, euroModel, predictions) {
    const out = [];
    for (const f of goalsApi.uniqueFixtures(predictions, goalsModel)) {
      const club = goalsApi.predict(goalsModel, f.league, f.home, f.away);
      const euro = euroModel && euroApi ? euroApi.predict(euroModel, f.league, f.home, f.away) : null;
      if (!club && !euro) continue;
      out.push({
        ...f,
        kind: club ? 'club' : 'euro',
        matchKey: goalsApi.canonicalFixtureKey(goalsModel, f.league, f.home, f.away),
      });
    }
    return out;
  }

  const api = { DISPLAY_TOTAL_LINES, pickEuro365View, pickToaView, pickFdoView, sameDayGate, buildRows, analyzableFixtures };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  // ---- Browser rendering on goals-model.html ----
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  const host = document.getElementById('day-odds-rows');
  if (!host || !goalsApi || !expressApi) return;
  window.DayOdds = api;

  const $ = id => document.getElementById(id);
  const fmtTime = t => new Date(t).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  function cell(tr, value) { const td = document.createElement('td'); td.textContent = value; tr.append(td); return td; }
  function linesCell(tr, lines) {
    const td = document.createElement('td');
    td.className = 'probability-cell';
    if (!lines.length) { td.textContent = '—'; tr.append(td); return td; }
    for (const item of lines) {
      const div = document.createElement('div');
      div.className = 'probability-line';
      div.textContent = item.text;
      if (item.title) div.title = item.title;
      td.append(div);
    }
    tr.append(td);
    return td;
  }

  function rowLines(r) {
    const x2 = [];
    if (r.euro365 && r.euro365.o1x2) {
      const o = r.euro365.o1x2;
      x2.push({ text: 'E365: ' + o.p1 + ' · ' + o.x + ' · ' + o.p2,
        title: 'Euro365: текущая интрадей-цена одного букмекера (П1 · Х · П2)' });
    }
    if (r.fdo && r.fdo.o1x2) {
      const o = r.fdo.o1x2;
      x2.push({ text: 'FDO open: ' + o.p1 + ' · ' + o.x + ' · ' + o.p2,
        title: 'football-data.co.uk: консенсус открытия рынка (Avg), справочно — цены могли уйти' });
    }
    const dc = [];
    if (r.euro365 && r.euro365.dc) {
      const d = r.euro365.dc;
      dc.push({ text: '1X ' + d['1X'] + ' · 12 ' + d['12'] + ' · X2 ' + d.X2,
        title: 'Euro365: двойные шансы, текущая цена' });
    }
    const totals = [];
    for (const line of DISPLAY_TOTAL_LINES) {
      const parts = [];
      const title = [];
      const e = r.euro365 && r.euro365.totals && r.euro365.totals[line];
      if (e) { parts.push('E365 ' + e.over + '/' + e.under); title.push('E365 — текущая цена Euro365'); }
      const t = r.toa && r.toa.totals && r.toa.totals[line];
      if (t) {
        parts.push('TOA ' + t.over + '/' + t.under);
        title.push('TOA — лучшая цена (Max) The Odds API' +
          (t.avgOver && t.avgUnder ? ', консенсус Avg ' + t.avgOver + '/' + t.avgUnder : ''));
      }
      const f = r.fdo && r.fdo.totals && r.fdo.totals[line];
      if (f) { parts.push('FDO ' + f.over + '/' + f.under); title.push('FDO — консенсус открытия (Avg), справочно'); }
      if (parts.length) totals.push({ text: 'ТБ/ТМ ' + line + ': ' + parts.join(' · '), title: title.join('\n') });
    }
    return { x2, dc, totals };
  }

  async function load() {
    const status = $('day-odds-status');
    status.textContent = 'Загрузка кэфов…';
    try {
      const [dataRes, goalsRes, euroRes, oddsRes, fdoRes, toaRes] = await Promise.all([
        fetch('./data.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data/goals_markets_models.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data/euro_cups_model.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data/odds.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data/odds-fdo.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data/odds-theodds.json?t=' + Date.now(), { cache: 'no-store' }),
      ]);
      if (!dataRes.ok) throw Error('data.json: HTTP ' + dataRes.status);
      if (!goalsRes.ok) throw Error('goals_markets_models.json: HTTP ' + goalsRes.status);
      const data = await dataRes.json();
      const goalsModel = await goalsRes.json();
      const euroModel = euroRes.ok ? await euroRes.json() : null;

      let oddsData = null;
      if (oddsRes.ok) {
        const candidate = await oddsRes.json();
        if (sameDayGate(candidate, data.date) && candidate.events) oddsData = candidate;
      }
      let toaData = null;
      if (toaRes.ok) {
        const candidate = await toaRes.json();
        if (sameDayGate(candidate, data.date) && Array.isArray(candidate.matches)) toaData = candidate;
      }
      // FDO без дневного гейта: файл содержит только предстоящие матчи, связка
      // идёт по ключам команд, а не по дате (как на странице экспрессов).
      let fdoData = null;
      if (fdoRes.ok) {
        const candidate = await fdoRes.json();
        if (candidate && Array.isArray(candidate.matches)) fdoData = candidate;
      }

      const fixtures = analyzableFixtures(goalsModel, euroModel, data.predictions);
      const eventIndex = expressApi.buildEventIndex(goalsModel, data.predictions);
      const fdoIndex = expressApi.buildFdoIndex(goalsModel, fdoData);
      const oddsApiIndex = expressApi.buildOddsApiIndex(goalsModel, toaData);
      const rows = buildRows(fixtures, {
        events: oddsData ? oddsData.events : null,
        eventIndex, fdoIndex, oddsApiIndex,
      });

      host.replaceChildren();
      for (const r of rows) {
        const tr = document.createElement('tr');
        cell(tr, fmtTime(r.time));
        cell(tr, r.league);
        cell(tr, r.match);
        const lines = rowLines(r);
        linesCell(tr, lines.x2);
        linesCell(tr, lines.dc);
        linesCell(tr, lines.totals);
        host.append(tr);
      }

      const clubCount = rows.filter(r => r.kind === 'club').length;
      const sources = [];
      if (oddsData) sources.push('Euro365 от ' + (oddsData.generatedAt || '—').slice(0, 16).replace('T', ' ') + ' UTC');
      if (toaData) sources.push('The Odds API от ' + (toaData.fetchedAt || '—').slice(0, 16).replace('T', ' ') + ' UTC');
      if (fdoData) sources.push('FDO (консенсус открытия, справочно) от ' + (fdoData.downloadedAt || '—').slice(0, 16).replace('T', ' ') + ' UTC');
      $('day-odds-count').textContent = 'Анализируемых матчей впереди: ' + fixtures.length +
        ', с кэфами хотя бы одного источника: ' + rows.length +
        ' (клубные: ' + clubCount + ', еврокубки: ' + (rows.length - clubCount) + '). ' +
        (sources.length ? 'Источники: ' + sources.join(' · ') + '.' : 'Актуальные кэфы сегодня недоступны.');
      $('day-odds-empty').hidden = rows.length > 0;
      status.textContent = rows.length ? 'Кэфы дня загружены.' : 'Кэфы дня: нет матчей с доступными ценами.';
    } catch (e) {
      status.textContent = 'Кэфы дня недоступны: ' + e.message;
      $('day-odds-empty').hidden = false;
    }
  }

  const refreshButton = $('refresh');
  if (refreshButton) refreshButton.addEventListener('click', load);
  load();
})();
