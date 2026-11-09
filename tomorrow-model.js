/* Страница «Прогнозы на завтра» (tomorrow-model.html).
 *
 * Прогнозы внутренних моделей на матчи ЗАВТРАШНЕГО дня (МСК): клубные лиги
 * модели голов, еврокубки и Лига наций. Список матчей — собственный артефакт
 * data/fixtures-tomorrow.json (scripts/models/fetch-tomorrow-fixtures.py,
 * прематч-линия Euro365, только расписание — без кэфов). Вероятности считают
 * те же модели, что и на основных страницах, из истории команд — прогноз на
 * завтра ничем не отличается от прогноза на сегодня. Кэфы на этой странице
 * сознательно не показываются. Вероятности не выдумываются: матч, который ни
 * одна модель оценить не может (лига или команда неизвестны), не выводится.
 */
(() => {
  'use strict';

  const goalsApi = (typeof module !== 'undefined' && module.exports)
    ? require('./goals-internal-model.js')
    : (typeof window !== 'undefined' ? window.GoalsInternalModel : null);
  const euroApi = (typeof module !== 'undefined' && module.exports)
    ? require('./euro-internal-model.js')
    : (typeof window !== 'undefined' ? window.EuroInternalModel || null : null);
  const ntApi = (typeof module !== 'undefined' && module.exports)
    ? require('./nt-internal-model.js')
    : (typeof window !== 'undefined' ? window.NtInternalModel || null : null);

  const DAY_MS = 24 * 60 * 60 * 1000;

  // 'YYYY-MM-DD' московского календарного дня для произвольного момента.
  function moscowDayKey(moment) {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(moment);
  }

  function tomorrowKey(now) {
    return moscowDayKey(new Date(now.getTime() + DAY_MS));
  }

  // Целевая дата страницы: ?date=YYYY-MM-DD (ручной прогноз на конкретный
  // день, напр. Лига наций в четверг) или по умолчанию завтрашний день МСК.
  function resolveTargetDate(now, param) {
    if (typeof param === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(param)) return param;
    return tomorrowKey(now);
  }

  // Артефакт расписания под целевую дату: завтра читается из ежедневного
  // fixtures-tomorrow.json, произвольная дата — из data/fixtures-<date>.json
  // (workflow «Fetch fixtures for a specific date»).
  function artifactPath(target, defaultTarget) {
    return target === defaultTarget
      ? './data/fixtures-tomorrow.json'
      : './data/fixtures-' + target + '.json';
  }

  // Классификация матча: первая модель, способная его оценить, забирает его.
  // Порядок клубы → еврокубки → сборные безопасен, потому что каждая модель
  // гейтит по своим лигам/турнирам и командам и честно возвращает null.
  function classifyFixture(apis, models, fixture) {
    const club = apis.goals && models.goals
      ? apis.goals.predict(models.goals, fixture.league, fixture.home, fixture.away)
      : null;
    if (club) return { kind: 'club', prediction: club };
    const euro = apis.euro && models.euro
      ? apis.euro.predict(models.euro, fixture.league, fixture.home, fixture.away)
      : null;
    if (euro) return { kind: 'euro', prediction: euro };
    const nt = apis.nt && models.nt
      ? apis.nt.predict(models.nt, fixture.league, fixture.home, fixture.away)
      : null;
    if (nt) return { kind: 'nt', prediction: nt };
    return null;
  }

  // Раскладка списка матчей по моделям; неоценённые честно считаются и в
  // таблицы не попадают.
  function buildPredictions(apis, fixtures, models) {
    const groups = { club: [], euro: [], nt: [] };
    let unscored = 0;
    for (const fixture of fixtures || []) {
      const hit = classifyFixture(apis, models, fixture);
      if (!hit) { unscored += 1; continue; }
      groups[hit.kind].push({ fixture, prediction: hit.prediction });
    }
    return { groups, unscored };
  }

  const api = { moscowDayKey, tomorrowKey, resolveTargetDate, artifactPath, classifyFixture, buildPredictions };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  // ---- Browser rendering на tomorrow-model.html ----
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  const clubHost = document.getElementById('tomorrow-club-rows');
  if (!clubHost || !goalsApi) return;
  window.TomorrowModel = api;

  const $ = id => document.getElementById(id);
  const fmtTime = t => new Date(t).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const pct = n => Number.isFinite(n) ? (n * 100).toFixed(1) + '%' : '—';
  // Единый с другими страницами градиент: чем выше вероятность, тем насыщеннее зелёный.
  const heat = p => { const x = Math.max(0, Math.min(1, p)), a = [240, 253, 244], b = [20, 83, 45]; return 'rgb(' + a.map((v, i) => Math.round(v + (b[i] - v) * x)).join(', ') + ')'; };

  function cell(tr, value) { const td = document.createElement('td'); td.textContent = value; tr.append(td); return td; }
  function stackedCell(tr, items) {
    const td = document.createElement('td');
    td.className = 'probability-cell';
    for (const item of items) {
      const line = document.createElement('div');
      line.className = 'probability-line';
      line.textContent = item.label + ' ' + pct(item.value);
      if (Number.isFinite(item.value)) {
        line.style.backgroundColor = heat(item.value);
        line.title = 'Вероятность: ' + pct(item.value);
        if (item.value > 0.65) line.classList.add('is-highlighted');
      }
      td.append(line);
    }
    tr.append(td);
    return td;
  }

  function renderClubRow(host, entry) {
    const f = entry.fixture, p = entry.prediction;
    const tr = document.createElement('tr');
    cell(tr, fmtTime(f.time));
    cell(tr, f.league);
    cell(tr, f.home + ' — ' + f.away);
    stackedCell(tr, [
      { label: 'П1', value: p.outcomes.p1 },
      { label: 'X', value: p.outcomes.x },
      { label: 'П2', value: p.outcomes.p2 },
      { label: '1X', value: p.outcomes.dc1x },
      { label: '12', value: p.outcomes.dc12 },
      { label: 'X2', value: p.outcomes.dcx2 },
    ]);
    stackedCell(tr, p.totalGoals.flatMap(t => [
      { label: 'ТБ ' + t.line, value: t.over },
      { label: 'ТМ ' + t.line, value: t.under },
    ]));
    stackedCell(tr, p.teamGoals.home.flatMap(t => [
      { label: 'ИТБ1 ' + t.line, value: t.over },
      { label: 'ИТМ1 ' + t.line, value: t.under },
    ]));
    stackedCell(tr, p.teamGoals.away.flatMap(t => [
      { label: 'ИТБ2 ' + t.line, value: t.over },
      { label: 'ИТМ2 ' + t.line, value: t.under },
    ]));
    host.append(tr);
  }

  function renderSimpleRow(host, entry) {
    const f = entry.fixture, m = entry.prediction.markets;
    const t25 = m.totals['2.5'] || m.totals[2.5];
    const tr = document.createElement('tr');
    cell(tr, fmtTime(f.time));
    cell(tr, f.league);
    cell(tr, f.home + ' — ' + f.away);
    stackedCell(tr, [
      { label: 'П1', value: m.p1 },
      { label: 'Х', value: m.x },
      { label: 'П2', value: m.p2 },
    ]);
    stackedCell(tr, [
      { label: '1X', value: m.dc1x },
      { label: '12', value: m.dc12 },
      { label: 'X2', value: m.dcx2 },
    ]);
    stackedCell(tr, t25 ? [
      { label: 'ТБ 2.5', value: t25.over },
      { label: 'ТМ 2.5', value: t25.under },
    ] : []);
    host.append(tr);
  }

  async function load() {
    const status = $('tomorrow-status');
    status.textContent = 'Загрузка прогнозов…';
    try {
      const dateParam = new URLSearchParams(window.location.search).get('date');
      const target = resolveTargetDate(new Date(), dateParam);
      const path = artifactPath(target, tomorrowKey(new Date()));
      const [fixturesRes, goalsRes, euroRes, ntRes] = await Promise.all([
        fetch(path + '?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data/goals_markets_models.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data/euro_cups_model.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data/nt_model.json?t=' + Date.now(), { cache: 'no-store' }),
      ]);
      if (!fixturesRes.ok) throw Error(path.split('/').pop() + ': HTTP ' + fixturesRes.status);
      if (!goalsRes.ok) throw Error('goals_markets_models.json: HTTP ' + goalsRes.status);
      const artifact = await fixturesRes.json();
      const models = {
        goals: await goalsRes.json(),
        euro: euroRes.ok ? await euroRes.json() : null,
        nt: ntRes.ok ? await ntRes.json() : null,
      };

      // Дневной гейт: артефакт обязан описывать именно целевой день (МСК),
      // иначе показываем честное пустое состояние, а не вчерашний список.
      const fixtures = artifact && artifact.date === target && Array.isArray(artifact.fixtures)
        ? artifact.fixtures
        : [];

      const { groups, unscored } = buildPredictions({ goals: goalsApi, euro: euroApi, nt: ntApi }, fixtures, models);

      clubHost.replaceChildren();
      $('tomorrow-euro-rows').replaceChildren();
      $('tomorrow-nt-rows').replaceChildren();
      for (const entry of groups.club) renderClubRow(clubHost, entry);
      for (const entry of groups.euro) renderSimpleRow($('tomorrow-euro-rows'), entry);
      for (const entry of groups.nt) renderSimpleRow($('tomorrow-nt-rows'), entry);

      $('tomorrow-club-empty').hidden = groups.club.length > 0;
      $('tomorrow-euro-empty').hidden = groups.euro.length > 0;
      $('tomorrow-nt-empty').hidden = groups.nt.length > 0;

      const scored = groups.club.length + groups.euro.length + groups.nt.length;
      $('tomorrow-count').textContent = 'Целевая дата: ' + target + ' (МСК). ' +
        (fixtures.length
          ? 'Матчей в списке: ' + fixtures.length + ', оценено моделями: ' + scored +
            ' (клубные: ' + groups.club.length + ', еврокубки: ' + groups.euro.length +
            ', сборные: ' + groups.nt.length + '); без оценки (лига или команда неизвестны моделям): ' + unscored + '.'
          : 'Список матчей на ' + target + ' ещё не загружен или пуст (артефакт от ' +
            (artifact && artifact.date ? artifact.date : '—') + '). Расписание завтрашнего дня обновляется каждые 2 часа; произвольная дата — по запросу через ?date=ГГГГ-ММ-ДД.');
      status.textContent = fixtures.length ? 'Прогнозы загружены (' + target + ' МСК).' : 'Прогнозов на ' + target + ' пока нет.';
    } catch (e) {
      status.textContent = 'Прогнозы недоступны: ' + e.message;
      $('tomorrow-club-empty').hidden = false;
      $('tomorrow-euro-empty').hidden = false;
      $('tomorrow-nt-empty').hidden = false;
    }
  }

  const refreshButton = $('refresh');
  if (refreshButton) refreshButton.addEventListener('click', load);
  load();
})();
