(() => {
  'use strict';

  // Internal experimental corners/yellow-cards model runtime.
  // Consumes data/corners_cards_models.json (schemaVersion 2) produced by
  // scripts/models/export-corners-cards-runtime.py and scores upcoming fixtures
  // from data.json. Unknown leagues/teams never get an invented probability.

  const norm = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/&/g, ' and ').replace(/\b(fc|afc|cf|sc|sv|fk|sk|club|the)\b/g, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');

  // Curated aliases: normalized external name -> normalized historical (football-data.co.uk) name.
  // Applied only inside an already resolved league; ambiguous names stay unresolved.
  const TEAM_ALIASES = {
    'newcastle united': 'newcastle',
    'manchester united': 'man united',
    'manchester city': 'man city',
    'nottingham forest': 'nott m forest',
    'wolverhampton wanderers': 'wolves',
    'wolverhampton': 'wolves',
    'brighton and hove albion': 'brighton',
    'tottenham hotspur': 'tottenham',
    'west ham united': 'west ham',
    'leeds united': 'leeds',
    'leicester city': 'leicester',
    'sheffield wednesday': 'sheffield weds',
    'queens park rangers': 'qpr',
    'cardiff city': 'cardiff',
    'deportivo de a coruna': 'la coruna',
    'deportivo la coruna': 'la coruna',
    'rc deportivo la coruna': 'la coruna',
    'deportivo': 'la coruna',
    'atletico madrid': 'ath madrid',
    'atletico de madrid': 'ath madrid',
    'athletic bilbao': 'ath bilbao',
    'athletic club': 'ath bilbao',
    'athletic': 'ath bilbao',
    'real sociedad': 'sociedad',
    'rayo vallecano': 'vallecano',
    'real betis': 'betis',
    'real betis balompie': 'betis',
    'celta vigo': 'celta',
    'rc celta': 'celta',
    'sporting gijon': 'sp gijon',
    'real valladolid': 'valladolid',
    'cadiz': 'cadiz',
    'stade brestois 29': 'brest',
    'stade brestois': 'brest',
    'brestois': 'brest',
    'psg': 'paris sg',
    'paris saint germain': 'paris sg',
    'paris saint germain fc': 'paris sg',
    'olympique lyon': 'lyon',
    'olympique lyonnais': 'lyon',
    'olympique marseille': 'marseille',
    'olympique de marseille': 'marseille',
    'saint etienne': 'st etienne',
    'as saint etienne': 'st etienne',
    'as monaco': 'monaco',
    'bayern munchen': 'bayern munich',
    'fc bayern munchen': 'bayern munich',
    'borussia dortmund': 'dortmund',
    'borussia monchengladbach': 'm gladbach',
    'borussia m gladbach': 'm gladbach',
    'mochengladbach': 'm gladbach',
    'bayer 04 leverkusen': 'leverkusen',
    'bayer leverkusen': 'leverkusen',
    'eintracht frankfurt': 'ein frankfurt',
    'vfl wolfsburg': 'wolfsburg',
    'vfl bochum': 'bochum',
    '1 fc koln': 'koln',
    'fc koln': 'koln',
    'fc cologne': 'koln',
    'hertha bsc': 'hertha',
    'hertha berlin': 'hertha',
    'ac milan': 'milan',
    'internazionale': 'inter',
    'inter milan': 'inter',
    'hellas verona': 'verona',
    'ss lazio': 'lazio',
    'as roma': 'roma',
    'acf fiorentina': 'fiorentina',
    'uc sampdoria': 'sampdoria',
    'us lecce': 'lecce',
    'stade rennais': 'rennes',
    'stade de reims': 'reims',
    'rc lens': 'lens',
    'rc strasbourg': 'strasbourg',
    'estac troyes': 'troyes',
    'sco angers': 'angers',
    'ogc nice': 'nice',
    'fc nantes': 'nantes',
    'eintracht braunschweig': 'braunschweig',
    // Extended league set (2026-11): Belgium, Portugal, Turkey, Greece,
    // second divisions of England's peers (D2/I2/SP2/F2) and Scotland SC1.
    'sporting': 'sp lisbon',
    'sporting cp': 'sp lisbon',
    'sporting lisbon': 'sp lisbon',
    'vitoria': 'guimaraes',
    'vitoria guimaraes': 'guimaraes',
    'sporting braga': 'sp braga',
    'braga': 'sp braga',
    'estrela amadora': 'estrela',
    'estrela da amadora': 'estrela',
    'avs futebol sad': 'avs',
    'avs sad': 'avs',
    'academico de viseu': 'academico viseu',
    'union sg': 'st gilloise',
    'union st gilloise': 'st gilloise',
    'union saint gilloise': 'st gilloise',
    'royale union saint gilloise': 'st gilloise',
    'oh leuven': 'oud heverlee leuven',
    'oud heverlee': 'oud heverlee leuven',
    'zulte waregem': 'waregem',
    'beerschot': 'beerschot va',
    'basaksehir': 'buyuksehyr',
    'istanbul basaksehir': 'buyuksehyr',
    'istanbul buyuksehir': 'buyuksehyr',
    'adana demirspor': 'ad demirspor',
    'fatih karagumruk': 'karagumruk',
    'olympiacos': 'olympiakos',
    'olympiacos piraeus': 'olympiakos',
    'olympiakos piraeus': 'olympiakos',
    'aek athens': 'aek',
    'ofi': 'ofi crete',
    'nfc volos': 'volos nfc',
    'levadiakos': 'levadeiakos',
    'ael': 'larisa',
    'ael larissa': 'larisa',
    'larissa': 'larisa',
    'celta vigo b': 'celta b',
    'racing santander': 'santander',
    'racing ferrol': 'ferrol',
    'stade lavallois': 'laval',
    'lavallois': 'laval',
    'clermont foot': 'clermont',
    'preussen munster': 'preußen munster',
    'dynamo dresden': 'dresden',
    'energie cottbus': 'cottbus',
    'hamburger sv': 'hamburg',
    'hamburger': 'hamburg',
    'schalke': 'schalke 04',
    'arminia bielefeld': 'bielefeld',
    'airdrieonians': 'airdrie utd',
    'airdrie united': 'airdrie utd',
    'inverness': 'inverness c',
    'inverness ct': 'inverness c',
    'inverness caledonian thistle': 'inverness c',
    'greenock morton': 'morton',
    'queen s park': 'queens park',
    'raith rovers': 'raith rvs',
  };

  const factorial = [1];
  for (let i = 1; i <= 60; i++) factorial[i] = factorial[i - 1] * i;

  function poissonOver(lambda, line) {
    // P(X > line) with integer threshold floor(line); lines here end with .5.
    const k = Math.floor(line);
    let cumulative = 0;
    for (let i = 0; i <= k; i++) {
      cumulative += Math.exp(-lambda) * Math.pow(lambda, i) / factorial[i];
    }
    return Math.min(1, Math.max(0, 1 - cumulative));
  }

  function resolveLeague(model, leagueName) {
    const key = norm(leagueName);
    if (!key) return null;
    const aliases = model.leagueAliases || {};
    let code = aliases[key] || null;
    if (!code) {
      for (const [candidate, label] of Object.entries(model.leagueNames || {})) {
        if (norm(label) === key) { code = candidate; break; }
      }
    }
    if (code && model.teams && model.teams[code]) return code;
    return null;
  }

  function resolveTeam(model, leagueCode, teamName) {
    const teams = (model.teams || {})[leagueCode];
    if (!teams) return null;
    const key = norm(teamName);
    if (!key) return null;
    if (teams[key]) return { key, entry: teams[key], via: 'exact' };
    const alias = TEAM_ALIASES[key];
    if (alias && teams[alias]) return { key: alias, entry: teams[alias], via: 'alias' };
    // Unique substring fallback, both directions, minimum length to avoid accidents.
    const candidates = Object.keys(teams).filter(candidate => {
      if (candidate.length < 4 || key.length < 4) return false;
      return candidate.includes(key) || key.includes(candidate);
    });
    if (candidates.length === 1) {
      return { key: candidates[0], entry: teams[candidates[0]], via: 'unique-substring' };
    }
    return null;
  }

  function buildFeatureContext(model, leagueCode, home, away) {
    const leagueStats = (model.leagueStats || {})[leagueCode] || {};
    const hs = home.entry.stats || {};
    const as = away.entry.stats || {};
    const ctx = {
      home_matches_before: home.entry.matchesBefore,
      away_matches_before: away.entry.matchesBefore,
      league_matches_before: leagueStats.league_matches_before,
      home_advantage: 1,
      league_corners_avg: leagueStats.league_corners_avg,
      league_cards_avg: leagueStats.league_cards_avg,
      league_goals_avg: leagueStats.league_goals_avg,
      league_fouls_avg: leagueStats.league_fouls_avg,
    };
    for (const [side, stats] of [['home', hs], ['away', as]]) {
      for (const prefix of ['goal_team', 'team', 'card_team', 'foul_team', 'shots_team', 'shots_on_target_team']) {
        for (const kind of ['for', 'against']) {
          for (const window of [5, 10]) {
            ctx[`${side}_${prefix}_${kind}_avg${window}`] = stats[`${prefix}_${kind}_avg${window}`];
          }
        }
      }
    }
    // Referee is unknown pre-match; leave undefined so training medians are imputed.
    return ctx;
  }

  function predictLambda(spec, ctx, leagueCode) {
    const features = spec.features || [];
    let z = spec.intercept;
    for (let i = 0; i < features.length; i++) {
      const name = features[i];
      let value;
      if (name.endsWith('__missing')) {
        const base = name.slice(0, -'__missing'.length);
        value = ctx[base] === undefined || ctx[base] === null || Number.isNaN(ctx[base]) ? 1 : 0;
      } else if (name.startsWith('league__')) {
        value = name === 'league__' + leagueCode ? 1 : 0;
      } else {
        const raw = ctx[name];
        value = raw === undefined || raw === null || Number.isNaN(Number(raw))
          ? spec.imputerMedian[i] : Number(raw);
      }
      const scaled = (value - spec.scalerMean[i]) / (spec.scalerScale[i] || 1);
      z += spec.coefficients[i] * scaled;
    }
    return Math.min(40, Math.max(0.01, Math.exp(z)));
  }

  function predict(model, leagueName, homeName, awayName) {
    if (!model || model.schemaVersion !== 2 || !model.models) return null;
    const code = resolveLeague(model, leagueName);
    if (!code) return null;
    const home = resolveTeam(model, code, homeName);
    const away = resolveTeam(model, code, awayName);
    if (!home || !away) return null;
    const minMatches = (model.runtimePolicy && model.runtimePolicy.minimumTeamMatches) || 0;
    if (home.entry.matchesBefore < minMatches || away.entry.matchesBefore < minMatches) return null;
    const ctx = buildFeatureContext(model, code, home, away);
    const out = {
      league: code,
      leagueLabel: (model.leagueNames || {})[code] || code,
      homeModel: home.entry.name, awayModel: away.entry.name,
      homeVia: home.via, awayVia: away.via,
      trainingThrough: model.trainingThrough,
    };
    for (const [market, spec] of Object.entries(model.models)) {
      const lambda = predictLambda(spec, ctx, code);
      out[market] = {
        lambda,
        lines: (spec.lines || []).map(line => {
          const over = poissonOver(lambda, line);
          return { line, over, under: 1 - over };
        }),
      };
    }
    return out;
  }

  // Dedupe key: provider name variants of the same match collapse to the
  // model-resolved identity (league code + historical team keys). Raw names are
  // used only when the match is outside the model's coverage.
  function canonicalFixtureKey(model, leagueName, homeName, awayName) {
    const code = model ? resolveLeague(model, leagueName) : null;
    if (code) {
      const home = resolveTeam(model, code, homeName);
      const away = resolveTeam(model, code, awayName);
      if (home && away) return 'model|' + code + '|' + home.key + '|' + away.key;
    }
    return 'raw|' + norm(homeName) + '|' + norm(awayName);
  }

  function uniqueFixtures(predictions, model) {
    const map = new Map();
    for (const p of predictions || []) {
      const time = Date.parse(p.time);
      if (!Number.isFinite(time) || time <= Date.now()) continue;
      const home = norm(p.home), away = norm(p.away);
      if (!home || !away) continue;
      const key = canonicalFixtureKey(model, p.league, p.home, p.away);
      // Same match from several sources collapses to the earliest kickoff.
      if (!map.has(key) || time < map.get(key).time) {
        map.set(key, { time, home: p.home, away: p.away, league: p.league || '—' });
      }
    }
    return [...map.values()].sort((a, b) => a.time - b.time);
  }

  const api = { norm, poissonOver, resolveLeague, resolveTeam, buildFeatureContext, predictLambda, predict, canonicalFixtureKey, uniqueFixtures, TEAM_ALIASES };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  // ---- Browser rendering on model-v1.html ----
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  window.InternalModel = api;
  const host = document.getElementById('internal-rows');
  if (!host) return;

  const $ = id => document.getElementById(id);
  const pct = n => Number.isFinite(n) ? (n * 100).toFixed(1) + '%' : '—';
  const heat = p => { const x = Math.max(0, Math.min(1, p)), a = [240, 253, 244], b = [20, 83, 45]; return 'rgb(' + a.map((v, i) => Math.round(v + (b[i] - v) * x)).join(', ') + ')'; };
  const fmtTime = t => new Date(t).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  function cell(tr, value) { const td = document.createElement('td'); td.textContent = value; tr.append(td); return td; }
  function stackedCell(tr, items) {
    const td = document.createElement('td'); td.className = 'probability-cell';
    if (!items.length) { td.textContent = '—'; tr.append(td); return td; }
    for (const item of items) {
      const line = document.createElement('div'); line.className = 'probability-line';
      line.textContent = item.label + ': ' + pct(item.value);
      if (Number.isFinite(item.value)) {
        // Continuous gradient: the higher the probability, the stronger the color.
        line.style.backgroundColor = heat(item.value);
        line.title = 'Вероятность: ' + pct(item.value);
        if (item.value > .65) line.classList.add('is-highlighted');
      }
      td.append(line);
    }
    tr.append(td); return td;
  }

  async function load() {
    $('internal-status').textContent = 'Загрузка внутренней модели…';
    try {
      const [modelsResponse, dataResponse] = await Promise.all([
        fetch('./data/corners_cards_models.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data.json?t=' + Date.now(), { cache: 'no-store' }),
      ]);
      if (!modelsResponse.ok) throw Error('corners_cards_models.json: HTTP ' + modelsResponse.status);
      if (!dataResponse.ok) throw Error('data.json: HTTP ' + dataResponse.status);
      const model = await modelsResponse.json();
      const data = await dataResponse.json();
      const fixtures = uniqueFixtures(data.predictions, model);
      host.replaceChildren();
      let scored = 0;
      for (const fixture of fixtures) {
        const prediction = predict(model, fixture.league, fixture.home, fixture.away);
        if (!prediction) continue;
        scored++;
        const tr = document.createElement('tr');
        cell(tr, fmtTime(fixture.time));
        cell(tr, fixture.league);
        cell(tr, fixture.home + ' — ' + fixture.away);
        stackedCell(tr, prediction.corners.lines.flatMap(x => [
          { label: 'ТБ угл. ' + x.line, value: x.over },
          { label: 'ТМ угл. ' + x.line, value: x.under },
        ]));
        stackedCell(tr, prediction.yellowCards.lines.flatMap(x => [
          { label: 'ТБ ЖК ' + x.line, value: x.over },
          { label: 'ТМ ЖК ' + x.line, value: x.under },
        ]));
        host.append(tr);
      }
      const note = 'Модель: Poisson-регрессия по историческим данным football-data.co.uk, обучена по ' +
        (model.trainingThrough || '—') + '. Экспериментальная оценка, не рекомендация. ' +
        'Прогноз выводится только для матчей, где лига и обе команды найдены в обучающих данных.';
      $('internal-count').textContent = 'Матчей с прогнозом внутренней модели: ' + scored + '. ' + note;
      $('internal-empty').hidden = scored > 0;
      $('internal-status').textContent = 'Внутренняя модель загружена (снимок от ' + (model.trainingThrough || '—') + ').';
    } catch (e) {
      $('internal-status').textContent = 'Внутренняя модель недоступна: ' + e.message;
      $('internal-empty').hidden = false;
    }
  }
  const refreshButton = $('refresh');
  if (refreshButton) refreshButton.addEventListener('click', load);
  load();
})();
