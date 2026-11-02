(() => {
  'use strict';

  // Internal experimental expected-goals model runtime (1X2, double chance,
  // total goals, individual team totals). Consumes data/goals_markets_models.json
  // (schemaVersion 2) produced by scripts/models/export-goals-runtime.py and
  // scores upcoming fixtures from data.json. Unknown leagues/teams never get an
  // invented probability.

  const norm = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/&/g, ' and ').replace(/\b(fc|afc|cf|sc|sv|fk|sk|club|the)\b/g, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');

  // Same curated aliases as internal-model.js: normalized external name ->
  // normalized historical (football-data.co.uk) name.
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

  function poissonPmf(k, lambda) {
    return Math.exp(-lambda) * Math.pow(lambda, k) / factorial[k];
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
      ctx[`${side}_elo`] = stats.elo;
      ctx[`${side}_att_decay`] = stats.att_decay;
      ctx[`${side}_def_decay`] = stats.def_decay;
      for (const prefix of ['goal_team', 'team', 'card_team', 'foul_team', 'shots_team', 'shots_on_target_team']) {
        for (const kind of ['for', 'against']) {
          for (const window of [5, 10]) {
            ctx[`${side}_${prefix}_${kind}_avg${window}`] = stats[`${prefix}_${kind}_avg${window}`];
          }
        }
      }
    }
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
    return Math.min(8, Math.max(0.05, Math.exp(z)));
  }

  // Scoreline matrix over 0..maxGoals for each side, renormalized.
  // Isotonic calibrator knots: piecewise-linear interpolation, clamped outside.
  function calibrate(p, knots) {
    if (!knots || !knots.x || !knots.x.length) return p;
    const x = knots.x, y = knots.y;
    if (p <= x[0]) return y[0];
    const last = x.length - 1;
    if (p >= x[last]) return y[last];
    let lo = 0, hi = last;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (x[mid] <= p) lo = mid; else hi = mid;
    }
    const span = x[hi] - x[lo];
    const t = span > 1e-12 ? (p - x[lo]) / span : 0;
    return y[lo] + t * (y[hi] - y[lo]);
  }

  // Dixon-Coles low-score adjustment factor for one scoreline cell.
  function dcTau(x, y, homeLambda, awayLambda, rho) {
    if (x === 0 && y === 0) return 1 - homeLambda * awayLambda * rho;
    if (x === 0 && y === 1) return 1 + homeLambda * rho;
    if (x === 1 && y === 0) return 1 + awayLambda * rho;
    if (x === 1 && y === 1) return 1 - rho;
    return 1;
  }

  function deriveMarkets(homeLambda, awayLambda, totalLines, teamLines, maxGoals, totalsCalibration, dcRho) {
    const rho = Number.isFinite(dcRho) ? dcRho : 0;
    const matrix = [];
    let total = 0;
    for (let i = 0; i <= maxGoals; i++) {
      matrix[i] = [];
      for (let j = 0; j <= maxGoals; j++) {
        let p = poissonPmf(i, homeLambda) * poissonPmf(j, awayLambda);
        if (rho && i <= 1 && j <= 1) p *= Math.max(dcTau(i, j, homeLambda, awayLambda, rho), 1e-12);
        matrix[i][j] = p;
        total += p;
      }
    }
    let p1 = 0, x = 0, p2 = 0;
    const over = new Map(totalLines.map(line => [line, 0]));
    for (let i = 0; i <= maxGoals; i++) {
      for (let j = 0; j <= maxGoals; j++) {
        const p = matrix[i][j] / total;
        if (i > j) p1 += p; else if (i === j) x += p; else p2 += p;
        for (const line of totalLines) {
          if (i + j > line) over.set(line, over.get(line) + p);
        }
      }
    }
    const tail = (lambda, line) => {
      let cumulative = 0;
      for (let k = 0; k <= Math.floor(line); k++) cumulative += poissonPmf(k, lambda);
      return Math.min(1, Math.max(0, 1 - cumulative));
    };
    return {
      outcomes: {
        p1, x, p2,
        dc1x: p1 + x, dc12: p1 + p2, dcx2: x + p2,
      },
      totalGoals: totalLines.map(line => {
        let p = over.get(line);
        if (totalsCalibration && totalsCalibration[String(line)]) {
          p = Math.min(1, Math.max(0, calibrate(p, totalsCalibration[String(line)])));
        }
        return { line, over: p, under: 1 - p };
      }),
      teamGoals: {
        home: teamLines.map(line => { const p = tail(homeLambda, line); return { line, over: p, under: 1 - p }; }),
        away: teamLines.map(line => { const p = tail(awayLambda, line); return { line, over: p, under: 1 - p }; }),
      },
    };
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
    const homeLambda = predictLambda(model.models.homeGoals, ctx, code);
    const awayLambda = predictLambda(model.models.awayGoals, ctx, code);
    const lines = model.marketLines || {};
    const markets = deriveMarkets(homeLambda, awayLambda,
      lines.totalGoals || [1.5, 2.5, 3.5, 4.5],
      lines.teamGoals || [0.5, 1.5, 2.5],
      (model.runtimePolicy && model.runtimePolicy.maxGoals) || 10,
      model.totalsCalibration || null,
      model.dcRho || 0);
    return {
      league: code,
      leagueLabel: (model.leagueNames || {})[code] || code,
      homeModel: home.entry.name, awayModel: away.entry.name,
      homeVia: home.via, awayVia: away.via,
      trainingThrough: model.trainingThrough,
      homeLambda, awayLambda,
      ...markets,
    };
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

  const api = { norm, poissonPmf, calibrate, dcTau, resolveLeague, resolveTeam, buildFeatureContext, predictLambda, deriveMarkets, predict, canonicalFixtureKey, uniqueFixtures, TEAM_ALIASES };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  // ---- Browser rendering on goals-model.html ----
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  window.GoalsInternalModel = api;
  const host = document.getElementById('goals-internal-rows');
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
    $('goals-internal-status').textContent = 'Загрузка внутренней модели…';
    try {
      const [modelsResponse, dataResponse] = await Promise.all([
        fetch('./data/goals_markets_models.json?t=' + Date.now(), { cache: 'no-store' }),
        fetch('./data.json?t=' + Date.now(), { cache: 'no-store' }),
      ]);
      if (!modelsResponse.ok) throw Error('goals_markets_models.json: HTTP ' + modelsResponse.status);
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
        stackedCell(tr, [
          { label: 'П1', value: prediction.outcomes.p1 },
          { label: 'X', value: prediction.outcomes.x },
          { label: 'П2', value: prediction.outcomes.p2 },
          { label: '1X', value: prediction.outcomes.dc1x },
          { label: '12', value: prediction.outcomes.dc12 },
          { label: 'X2', value: prediction.outcomes.dcx2 },
        ]);
        stackedCell(tr, prediction.totalGoals.flatMap(t => [
          { label: 'ТБ ' + t.line, value: t.over },
          { label: 'ТМ ' + t.line, value: t.under },
        ]));
        stackedCell(tr, prediction.teamGoals.home.flatMap(t => [
          { label: 'ИТБ1 ' + t.line, value: t.over },
          { label: 'ИТМ1 ' + t.line, value: t.under },
        ]));
        stackedCell(tr, prediction.teamGoals.away.flatMap(t => [
          { label: 'ИТБ2 ' + t.line, value: t.over },
          { label: 'ИТМ2 ' + t.line, value: t.under },
        ]));
        host.append(tr);
      }
      const dcNote = model.dcRho
        ? ' с поправкой Dixon–Coles на низкие счета (ρ=' + model.dcRho + ')'
        : '';
      const note = 'Модель: две Poisson-регрессии ожидаемых голов' + dcNote + ' по историческим данным football-data.co.uk ' +
        '(среди признаков — Elo и затухающие рейтинги атаки/защиты), обучена по ' +
        (model.trainingThrough || '—') + '. Все рынки выводятся из одной матрицы счёта. Экспериментальная оценка, не рекомендация. ' +
        'Прогноз выводится только для матчей, где лига и обе команды найдены в обучающих данных.';
      $('goals-internal-count').textContent = 'Матчей с прогнозом внутренней модели: ' + scored + '. ' + note;
      $('goals-internal-empty').hidden = scored > 0;
      $('goals-internal-status').textContent = 'Внутренняя модель загружена (снимок от ' + (model.trainingThrough || '—') + ').';
    } catch (e) {
      $('goals-internal-status').textContent = 'Внутренняя модель недоступна: ' + e.message;
      $('goals-internal-empty').hidden = false;
    }
  }
  const refreshButton = $('refresh');
  if (refreshButton) refreshButton.addEventListener('click', load);
  load();
})();
