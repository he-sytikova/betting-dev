const $ = (id) => document.getElementById(id);
let allMarkets = [];
let productionModels = null;
function val(v) { return v === null || v === undefined || v === '' || v === '—' ? '—' : String(v); }
function fmtTime(v) {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('ru-RU', {timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
}
function normalize(value) {
  return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .normalize('NFKC').toLocaleLowerCase('ru')
    .replace(/\b(fc|afc|cf|sc|sv|fk|sk|club|the)\b/g, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
}
const TEAM_ALIASES = {
  I1: {
    'ac milan': 'milan',
    'internazionale milano': 'inter',
  },
  SP1: {
    'deportivo alaves': 'alaves',
  },
  N1: {
    'cambuur leeuwarden': 'cambuur',
    'az': 'az alkmaar',
  },
};
function findTeam(leagueCode, leagueTeams, fixtureName) {
  const normalized = normalize(fixtureName);
  const target = TEAM_ALIASES[leagueCode]?.[normalized] || normalized;
  const matches = Object.keys(leagueTeams || {}).filter(key =>
    normalize(key) === target || normalize(leagueTeams[key]?.name) === target
  );
  if (matches.length !== 1) return null;
  return { key: matches[0], team: leagueTeams[matches[0]] };
}
function setSourceStatus(id, name, status) {
  const el = $(id);
  if (!el) return;
  const count = Number(status?.count || 0);
  el.title = status?.message || status?.diagnostic || '';
  if (status?.ok) {
    el.textContent = name + ': ' + count + ' матчей';
    el.className = 'status-badge ok';
  } else {
    el.textContent = name + ': нет данных';
    el.className = 'status-badge error';
  }
}
function poissonOver(mean, line) {
  if (!Number.isFinite(mean) || mean <= 0) return null;
  const limit = Math.floor(line);
  let term = Math.exp(-mean), cumulative = term;
  for (let k = 1; k <= limit; k++) {
    term *= mean / k;
    cumulative += term;
  }
  return Math.max(0, Math.min(1, 1 - cumulative));
}
function predictMean(model, featureValues) {
  const z = model.features.map((name, i) => {
    const raw = featureValues[name];
    const value = Number.isFinite(raw) ? raw : model.imputerMedian[i];
    return (value - model.scalerMean[i]) / (model.scalerScale[i] || 1);
  });
  const eta = model.intercept + z.reduce((sum, value, i) => sum + value * model.coefficients[i], 0);
  return Math.max(0.05, Math.min(30, Math.exp(Math.max(-5, Math.min(5, eta)))));
}
function formatTotals(mean, market) {
  const lines = productionModels.marketLines?.[market] || productionModels.lines;
  const modelType = productionModels.models?.[market]?.type || 'unknown';
  const modelLabel = ['negative_binomial', 'negative-binomial', 'nb2'].includes(String(modelType).toLowerCase())
    ? 'NB2'
    : modelType === 'poisson' ? 'Poisson' : modelType;
  return lines.map(line => {
    const over = poissonOver(mean, line);
    if (over === null) return '';
    return 'ТБ ' + line + ': ' + (over * 100).toFixed(1) + '% / ТМ: ' + ((1 - over) * 100).toFixed(1) + '% (' + modelLabel + ')';
  }).filter(Boolean).join('\n');
}
function makeModelRows(fixtures) {
  if (!productionModels?.models || !productionModels?.teams || !productionModels?.leagueNames) return [];
  const leagueCodeByName = new Map(Object.entries(productionModels.leagueAliases || {})
    .map(([name, code]) => [normalize(name), code]));
  for (const [code, name] of Object.entries(productionModels.leagueNames || {})) {
    leagueCodeByName.set(normalize(name), code);
  }
  // Fixture feeds often prefix the same competition with its country.
  const leagueAliases = {
    'germany bundesliga': 'D1',
    'germany 2 bundesliga': 'D2',
    'italy serie a': 'I1',
    'italy serie b': 'I2',
    'spain la liga': 'SP1',
    'spain segunda': 'SP2',
    'france ligue 1': 'F1',
    'france ligue 2': 'F2',
    'netherlands eredivisie': 'N1',
    'england premier league': 'E0',
    'england championship': 'E1',
    'england league one': 'E2',
    'england league two': 'E3',
  };
  for (const [name, code] of Object.entries(leagueAliases)) {
    if (!leagueCodeByName.has(name)) leagueCodeByName.set(name, code);
  }
  const seen = new Set(), output = [];
  for (const fixture of fixtures) {
    const leagueCode = leagueCodeByName.get(normalize(fixture.league));
    if (!leagueCode) continue;
    const leagueTeams = productionModels.teams[leagueCode];
    const homeMatch = findTeam(leagueCode, leagueTeams, fixture.home);
    const awayMatch = findTeam(leagueCode, leagueTeams, fixture.away);
    const home = homeMatch?.team;
    const away = awayMatch?.team;
    const league = productionModels.leagueStats?.[leagueCode];
    if (!home || !away || !league) continue;
    // Deduplicate by the matched historical team IDs, not by provider display names.
    const key = [Date.parse(fixture.time), leagueCode, homeMatch.key, awayMatch.key].join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    const features = {
      home_team_for_avg5: home.stats.team_for_avg5,
      home_team_against_avg5: home.stats.team_against_avg5,
      home_team_for_avg10: home.stats.team_for_avg10,
      home_team_against_avg10: home.stats.team_against_avg10,
      away_team_for_avg5: away.stats.team_for_avg5,
      away_team_against_avg5: away.stats.team_against_avg5,
      away_team_for_avg10: away.stats.team_for_avg10,
      away_team_against_avg10: away.stats.team_against_avg10,
      home_card_team_for_avg5: home.stats.card_team_for_avg5,
      home_card_team_against_avg5: home.stats.card_team_against_avg5,
      home_card_team_for_avg10: home.stats.card_team_for_avg10,
      home_card_team_against_avg10: home.stats.card_team_against_avg10,
      away_card_team_for_avg5: away.stats.card_team_for_avg5,
      away_card_team_against_avg5: away.stats.card_team_against_avg5,
      away_card_team_for_avg10: away.stats.card_team_for_avg10,
      away_card_team_against_avg10: away.stats.card_team_against_avg10,
      league_corners_avg: league.league_corners_avg,
      league_cards_avg: league.league_cards_avg,
      home_advantage: 1,
    };
    const cornerMean = predictMean(productionModels.models.corners, features);
    const cardMean = predictMean(productionModels.models.yellowCards, features);
    output.push({
      source: 'Модель', time: fixture.time, league: fixture.league,
      home: fixture.home, away: fixture.away,
      corners: formatTotals(cornerMean, 'corners'),
      yellowCards: formatTotals(cardMean, 'yellowCards'),
    });
  }
  return output;
}
function colorMarketCells(row) {
  for (const cell of row.cells) {
    cell.style.backgroundColor = '#fff';
    cell.style.color = '#000';
    if (cell.cellIndex < 5) continue;
    cell.style.whiteSpace = 'pre-line';
    const nums = Array.from(cell.textContent.matchAll(/([0-9]+(?:[.,][0-9]+)?)\s*%/g), match =>
      Number.parseFloat(match[1].replace(',', '.'))
    ).filter(Number.isFinite);
    if (!nums.length) continue;
    const highest = Math.max(...nums);
    if (highest > 65) {
      const ratio = Math.max(0, Math.min(1, (highest - 65) / 35));
      const pale = [220, 252, 231], dark = [20, 83, 45];
      cell.style.backgroundColor = 'rgb(' + pale.map((start, i) => Math.round(start + (dark[i] - start) * ratio)).join(', ') + ')';
      cell.title = 'Вероятность: ' + highest.toFixed(1) + '%';
    }
  }
}
function render() {
  const type = $('market-type').value;
  const source = $('market-source').value;
  const query = $('market-search').value.trim().toLocaleLowerCase('ru');
  const rows = allMarkets.filter(x => {
    const hasCorners = val(x.corners) !== '—';
    const hasCards = val(x.yellowCards) !== '—';
    if (type === 'corners' && !hasCorners) return false;
    if (type === 'cards' && !hasCards) return false;
    if (source !== 'all' && x.source !== source) return false;
    const haystack = [x.league,x.home,x.away,x.source].join(' ').toLocaleLowerCase('ru');
    return !query || haystack.includes(query);
  }).sort((a,b) => (Date.parse(a.time)||0) - (Date.parse(b.time)||0));
  const tbody = $('markets-body');
  tbody.replaceChildren();
  for (const x of rows) {
    const tr = document.createElement('tr');
    for (const v of [fmtTime(x.time),val(x.league),val(x.home),val(x.away),val(x.source),val(x.corners),val(x.yellowCards)]) {
      const td = document.createElement('td');
      td.textContent = v;
      tr.appendChild(td);
    }
    colorMarketCells(tr);
    tbody.appendChild(tr);
  }
  $('markets-count').textContent = 'Матчей с прогнозами: ' + rows.length;
  $('markets-empty').classList.toggle('hidden', rows.length > 0);
}
async function load() {
  try {
    const [response, modelResponse] = await Promise.all([
      fetch('./data.json?t=' + Date.now(), {cache:'no-store'}),
      fetch('./data/corners_cards_models.json?t=' + Date.now(), {cache:'no-store'}).catch(() => null),
    ]);
    if (!response.ok) throw new Error('Не удалось загрузить data.json (HTTP ' + response.status + ')');
    const data = await response.json();
    if (modelResponse?.ok) {
      try { productionModels = await modelResponse.json(); }
      catch (_) { productionModels = null; }
    }
    const now = Date.now();
    const fixtures = (Array.isArray(data.predictions) ? data.predictions : []).filter(item => {
      const kickoff = Date.parse(item.time);
      return Number.isFinite(kickoff) && kickoff > now;
    });
    const externalRows = fixtures.filter(item => val(item.corners) !== '—' || val(item.yellowCards) !== '—');
    const modelRows = makeModelRows(fixtures);
    allMarkets = [...externalRows, ...modelRows];
    setSourceStatus('status-bsd','BSD',data.sources?.bsd);
    setSourceStatus('status-sstats','SStats',data.sources?.sstats);
    setSourceStatus('status-euro365','Euro365',data.sources?.euro365);
    setSourceStatus('status-fbdata','FB_DATA',data.sources?.fbdata);
    const generated = data.generatedAt ? new Date(data.generatedAt) : null;
    $('markets-date').textContent = 'Матчи на ' + (data.date || 'сегодня') + (generated && !Number.isNaN(generated.getTime()) ? ' · обновлено ' + generated.toLocaleString('ru-RU') : '');
    $('markets-count').textContent = productionModels ? 'Модель: ' + modelRows.length + ' сопоставленных матчей' : 'Модель не загружена — доступны внешние источники';
    render();
  } catch (e) {
    $('markets-error').textContent = e.message || 'Ошибка загрузки данных.';
    $('markets-error').classList.remove('hidden');
    $('markets-body').replaceChildren();
    $('markets-empty').classList.add('hidden');
  }
}
document.addEventListener('DOMContentLoaded', () => {
  ['market-type','market-source'].forEach(id => $(id).addEventListener('change',render));
  $('market-search').addEventListener('input',render);
  const refresh = $('refresh-btn');
  if (refresh) refresh.addEventListener('click', () => load());
  load();
});
