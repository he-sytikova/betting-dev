const $ = (id) => document.getElementById(id);
let allMarkets = [];
let productionModels = null;
let distributionConfig = null;
let modelDiagnostics = {total:0,generated:0,duplicates:0,excludedByReason:{},excludedSamples:[]};
function val(v) { return v === null || v === undefined || v === '' || v === '—' ? '—' : String(v); }
function fmtTime(v) {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('ru-RU', {timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
}
function normalize(value) {
  return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .normalize('NFKC').toLocaleLowerCase('ru').replace(/ı/g, 'i')
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
// Shared aliases are applied only within the already-resolved league.
// findTeam still requires exactly one historical team match, preventing fuzzy cross-team matches.
const COMMON_TEAM_ALIASES = {
  'ac milan': 'milan',
  'internazionale milano': 'inter',
  'deportivo alaves': 'alaves',
  'cambuur leeuwarden': 'cambuur',
  'az': 'az alkmaar',
  'pec zwolle': 'zwolle',
  'ado den haag': 'den haag',
  'como 1907': 'como',
  'unione venezia': 'venezia',
  'twente 65': 'twente',
  'telstar 1963': 'telstar',
  'sheffield wednesday': 'sheffield weds',
  'rc strasbourg': 'strasbourg',
  'rc strasbourg alsace': 'strasbourg',
  'aj auxerre': 'auxerre',
  'cagliari calcio': 'cagliari',
  'frosinone calcio': 'frosinone',
  'ss lazio': 'lazio',
  '1 union berlin': 'union berlin',
  '1 koln': 'koln',
  'spvgg greuther furth': 'greuther furth',
  'hannover 96': 'hannover',
  '1 nurnberg': 'nurnberg',
  'royal antwerp': 'antwerp',
  'sint truidense vv': 'st truiden',
  'sbv excelsior': 'excelsior',
  'us avellino 1912': 'avellino',
  'hellas verona': 'verona',
  'citta di palermo': 'palermo',
  'vicenza calcio': 'vicenza',
  'racing de santander': 'santander',
  'real racing': 'santander',
  'athletic': 'ath bilbao',
  'athletic club': 'ath bilbao',
  'real sociedad': 'sociedad',
  'nacional da madeira': 'nacional',
  'eintracht frankfurt': 'ein frankfurt',
  'hamburger sv': 'hamburg',
  'hamburger': 'hamburg',
  'angers sco': 'angers',
  'olympique lyonnais': 'lyon',
  'olympique de marseille': 'marseille',
  'as monaco': 'monaco',
  'as monaco fc': 'monaco',
  'genoa cfc': 'genoa',
  'ssc napoli': 'napoli',
  'us lecce': 'lecce',
  'juventus fc': 'juventus',
  'cd eldense': 'eldense',
  'ad ceuta': 'ceuta',
  'cd castellon': 'castellon',
  'cd tenerife': 'tenerife',
  'burgos de futbol': 'burgos',
  'holstein': 'holstein kiel',
  'lr vicenza': 'vicenza',
  'ascoli calcio 1898': 'ascoli',
  'avellino 1912': 'avellino',
  'carrarese calcio': 'carrarese',
  'levadiakos': 'levadeiakos',
  'apo levadiakos': 'levadeiakos',
  'aris thessaloniki': 'aris',
  'gfs panetolikos': 'panetolikos',
  'asteras aktor': 'asteras tripolis',
  'aek athens': 'aek',
  'panathinaikos fc': 'panathinaikos',
  'sint truidense': 'st truiden',
  'r charleroi': 'charleroi',
  'cd nacional': 'nacional',
  'cs maritimo': 'maritimo',
  'sporting braga': 'sp braga',
  'athletic bilbao': 'ath bilbao'
};
function findTeam(leagueCode, leagueTeams, fixtureName) {
  const normalized = normalize(fixtureName);
  const target = TEAM_ALIASES[leagueCode]?.[normalized] || COMMON_TEAM_ALIASES[normalized] || normalized;
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
function countOver(mean, line, distribution, alpha) {
  if (!Number.isFinite(mean) || mean <= 0) return null;
  const limit = Math.floor(line);
  let cumulative = 0;
  if (distribution === 'negativeBinomial') {
    if (!Number.isFinite(alpha) || alpha <= 0) return null;
    const size = 1 / alpha;
    const p = size / (size + mean);
    let term = Math.pow(p, size);
    cumulative = term;
    for (let k = 0; k < limit; k++) {
      term *= ((k + size) / (k + 1)) * (1 - p);
      cumulative += term;
    }
  } else {
    let term = Math.exp(-mean);
    cumulative = term;
    for (let k = 1; k <= limit; k++) {
      term *= mean / k;
      cumulative += term;
    }
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
  const marketConfig = distributionConfig.markets[market];
  const lines = productionModels.marketLines?.[market] || productionModels.lines;
  return lines.map(line => {
    const distribution = marketConfig.lines[String(line)] || 'poisson';
    const over = countOver(mean, line, distribution, marketConfig.alpha);
    if (over === null) return '';
    const label = distribution === 'negativeBinomial' ? 'NB2' : 'Poisson';
    return 'ТБ ' + line + ': ' + (over * 100).toFixed(1) + '%\nТМ ' + line + ': ' + ((1 - over) * 100).toFixed(1) + '% (' + label + ')';
  }).join('\n');
}
function makeModelRows(fixtures) {
  modelDiagnostics = {total:fixtures.length,generated:0,duplicates:0,excludedByReason:{},excludedSamples:[]};
  if (!productionModels?.models || !productionModels?.teams || !productionModels?.leagueNames) {
    modelDiagnostics.excludedByReason['Конфигурация модели неполная'] = fixtures.length;
    return [];
  }
  const leagueCandidates = new Map();
  const addLeagueCandidate = (name, code) => {
    const key = normalize(name);
    if (!key || !code) return;
    if (!leagueCandidates.has(key)) leagueCandidates.set(key, new Set());
    leagueCandidates.get(key).add(code);
  };
  for (const [name, code] of Object.entries(productionModels.leagueAliases || {})) addLeagueCandidate(name, code);
  for (const [code, name] of Object.entries(productionModels.leagueNames || {})) addLeagueCandidate(name, code);
  // Explicit competition aliases only; no partial-name or fuzzy league matching.
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
  for (const [name, code] of Object.entries(leagueAliases)) addLeagueCandidate(name, code);
  const seen = new Set(), output = [];
  const exclude = (fixture, reason) => {
    modelDiagnostics.excludedByReason[reason] = (modelDiagnostics.excludedByReason[reason] || 0) + 1;
    if (modelDiagnostics.excludedSamples.length < 100) {
      modelDiagnostics.excludedSamples.push({
        time:fixture.time,league:fixture.league,home:fixture.home,away:fixture.away,reason
      });
    }
  };
  for (const fixture of fixtures) {
    const candidates = leagueCandidates.get(normalize(fixture.league));
    if (!candidates || candidates.size !== 1) {
      exclude(fixture, candidates?.size > 1 ? 'Неоднозначное название лиги' : 'Лига не распознана');
      continue;
    }
    const leagueCode = candidates.values().next().value;
    const leagueTeams = productionModels.teams[leagueCode];
    if (!leagueTeams) {
      exclude(fixture, 'Нет исторической базы команд лиги');
      continue;
    }
    const league = productionModels.leagueStats?.[leagueCode];
    if (!league) {
      exclude(fixture, 'Нет статистики лиги');
      continue;
    }
    const homeMatch = findTeam(leagueCode, leagueTeams, fixture.home);
    const awayMatch = findTeam(leagueCode, leagueTeams, fixture.away);
    if (!homeMatch) {
      exclude(fixture, 'Домашняя команда не сопоставлена однозначно');
      continue;
    }
    if (!awayMatch) {
      exclude(fixture, 'Гостевая команда не сопоставлена однозначно');
      continue;
    }
    const home = homeMatch.team, away = awayMatch.team;
    if (!home?.stats || !away?.stats) {
      exclude(fixture, 'Нет командной статистики');
      continue;
    }
    // Deduplicate only after league and both historical team IDs are verified.
    const key = [Date.parse(fixture.time), leagueCode, homeMatch.key, awayMatch.key].join('|');
    if (seen.has(key)) {
      modelDiagnostics.duplicates++;
      continue;
    }
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
      source: 'Модель v1', time: fixture.time, league: fixture.league,
      home: fixture.home, away: fixture.away,
      corners: formatTotals(cornerMean, 'corners'),
      yellowCards: formatTotals(cardMean, 'yellowCards'),
    });
  }
  modelDiagnostics.generated = output.length;
  return output;
}
function colorMarketCells(row) {
  for (const cell of row.cells) {
    cell.style.backgroundColor = '#fff';
    cell.style.color = '#000';
    if (cell.cellIndex < 5) continue;
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
  const excludedCount = Object.values(modelDiagnostics.excludedByReason).reduce((a,b)=>a+b,0);
  $('markets-count').textContent = 'Прогнозы: ' + rows.length + ' из ' + allMarkets.length +
    ' уникальных матчей · исключено: ' + excludedCount + ' · дубликатов источников: ' + modelDiagnostics.duplicates;
  $('markets-empty').classList.toggle('hidden', rows.length > 0);
}
async function load() {
  try {
    const [response, modelResponse, distributionResponse] = await Promise.all([
      fetch('./data.json?t=' + Date.now(), {cache:'no-store'}),
      fetch('./data/corners_cards_models.json?t=' + Date.now(), {cache:'no-store'}).catch(() => null),
      fetch('./data/corners_cards_distribution_v1.json?t=' + Date.now(), {cache:'no-store'}).catch(() => null),
    ]);
    if (!response.ok) throw new Error('Не удалось загрузить data.json (HTTP ' + response.status + ')');
    const data = await response.json();
    if (modelResponse?.ok) {
      try { productionModels = await modelResponse.json(); }
      catch (_) { productionModels = null; }
    }
    if (distributionResponse?.ok) {
      try { distributionConfig = await distributionResponse.json(); }
      catch (_) { distributionConfig = null; }
    }
    if (!productionModels?.models || !productionModels?.teams || !distributionConfig?.markets?.corners || !distributionConfig?.markets?.yellowCards) {
      throw new Error('Не удалось загрузить конфигурацию гибридного распределения v1.');
    }
    for (const market of ['corners', 'yellowCards']) {
      const policy = distributionConfig.markets[market];
      if (!Number.isFinite(policy.alpha) || policy.alpha <= 0 ||
          !policy.lines || Object.values(policy.lines).some(value => !['poisson', 'negativeBinomial'].includes(value))) {
        throw new Error('Некорректная конфигурация распределения для рынка: ' + market);
      }
    }
    const now = Date.now();
    const fixtures = (Array.isArray(data.predictions) ? data.predictions : []).filter(item => {
      const kickoff = Date.parse(item.time);
      return Number.isFinite(kickoff) && kickoff > now;
    });
    const modelRows = makeModelRows(fixtures);
    allMarkets = modelRows;
    setSourceStatus('status-bsd','BSD',data.sources?.bsd);
    setSourceStatus('status-sstats','SStats',data.sources?.sstats);
    setSourceStatus('status-euro365','Euro365',data.sources?.euro365);
    setSourceStatus('status-fbdata','FB_DATA',data.sources?.fbdata);
    const generated = data.generatedAt ? new Date(data.generatedAt) : null;
    $('markets-date').textContent = 'Матчи на ' + (data.date || 'сегодня') + (generated && !Number.isNaN(generated.getTime()) ? ' · обновлено ' + generated.toLocaleString('ru-RU') : '');
    $('markets-count').textContent = productionModels
      ? 'Прогнозы: ' + modelRows.length + ' уникальных матчей · исключено: ' +
        Object.values(modelDiagnostics.excludedByReason).reduce((a,b)=>a+b,0) +
        ' · дубликатов источников: ' + modelDiagnostics.duplicates
      : 'Модель не загружена';
    const diagnostics = $('markets-diagnostics');
    if (diagnostics) {
      const reasons = Object.entries(modelDiagnostics.excludedByReason)
        .sort((a,b)=>b[1]-a[1]).map(([reason,count])=>count+' — '+reason);
      const samples = modelDiagnostics.excludedSamples.map(x =>
        x.time+' | '+x.league+' | '+x.home+' — '+x.away+' | '+x.reason);
      diagnostics.textContent = [...reasons,'',...samples].join('\\n') || 'Исключённых матчей нет.';
    }
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
