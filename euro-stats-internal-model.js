/* Еврокубковые внутренние модели статистики: угловые и жёлтые карточки
 * (Лига чемпионов, Лига Европы, Лига Конференций).
 *
 * Чистый рантайм: вероятности считаются из data/euro_stats_models.json
 * (экспорт scripts/models/export-euro-stats-runtime.py). Импьютинг, скейлинг
 * и коэффициенты воспроизводят sklearn-пайплайн экспортёра; распределение —
 * Poisson или NB2 по решению валидации; изотонная калибровка применяется,
 * если была внедрена. Гейтинг по турнирам — по списку competitions из самого
 * JSON; вероятности не выдумываются: неизвестный турнир или команда -> null.
 *
 * Форма ответа predict() совпадает с клубной моделью (internal-model.js):
 * prediction.corners / prediction.yellowCards = { lambda, distribution,
 * lines: [{line, over, under}] } — поэтому контур дня использует общий
 * clubMarketCandidates.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.EuroStatsInternalModel = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function norm(value) {
    return String(value || '')
      .normalize('NFKD').replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  }

  function competitionCatalog(model) {
    return (model && Array.isArray(model.competitions)) ? model.competitions : [];
  }

  // Сопоставляет название лиги из фида с турниром модели; неизвестный
  // турнир -> null (вероятности не выдумываются).
  function resolveCompetition(model, leagueName) {
    const n = norm(leagueName);
    if (!n) return null;
    const catalog = competitionCatalog(model);
    for (let i = 0; i < catalog.length; i++) {
      const comp = catalog[i];
      const names = comp.names || [];
      for (let j = 0; j < names.length; j++) {
        if (norm(names[j]) === n) return { code: comp.code, name: comp.name || comp.code };
      }
    }
    return null;
  }

  function resolveTeam(model, name) {
    const key = (model.teamAliases || {})[norm(name)];
    return key ? { key, team: (model.teams || {})[key] } : null;
  }

  // Контекст признаков: те же имена, что генерирует feature_columns()
  // train-euro-stats.py. Отсутствующие значения — null (уйдут в медиану
  // импьютера с индикатором пропуска).
  function buildContext(model, comp, home, away) {
    const hE = home.team.euro || {};
    const aE = away.team.euro || {};
    const hD = home.team.domestic || null;
    const aD = away.team.domestic || null;
    const ctx = {
      home_euro_elo: hE.euroElo != null ? hE.euroElo : null,
      away_euro_elo: aE.euroElo != null ? aE.euroElo : null,
      home_euro_gf: hE.euroGf != null ? hE.euroGf : null,
      home_euro_ga: hE.euroGa != null ? hE.euroGa : null,
      away_euro_gf: aE.euroGf != null ? aE.euroGf : null,
      away_euro_ga: aE.euroGa != null ? aE.euroGa : null,
      comp_is_el: comp.code === 'EL' ? 1 : 0,
      comp_is_ecl: comp.code === 'ECL' ? 1 : 0,
      home_dom_missing: hD ? 0 : 1,
      away_dom_missing: aD ? 0 : 1,
    };
    const groups = ['corners', 'cards'];
    for (const group of groups) {
      const compAvg = model.compAverages && model.compAverages[comp.code];
      ctx[`comp_${group}_avg`] = compAvg && compAvg[group] != null ? compAvg[group] : null;
      for (const [side, euro, dom] of [['home', hE, hD], ['away', aE, aD]]) {
        for (const kind of ['for', 'against']) {
          for (const window of [5, 10]) {
            const euroKey = `euro_${group}_${kind}_avg${window}`;
            ctx[`${side}_${euroKey}`] = euro[euroKey] != null ? euro[euroKey] : null;
            const domKey = `${group}_${kind}_avg${window}`;
            ctx[`${side}_dom_${domKey}`] = dom && dom[domKey] != null ? dom[domKey] : null;
          }
        }
        const gamesKey = `euro_${group}_games`;
        ctx[`${side}_${gamesKey}`] = euro[gamesKey] != null ? euro[gamesKey] : null;
      }
    }
    return ctx;
  }

  // Воспроизводит SimpleImputer(median, add_indicator, keep_empty_features)
  // + StandardScaler по экспортированным параметрам.
  function transform(spec, ctx) {
    return spec.features.map((name, i) => {
      let raw;
      if (name.endsWith('__missing')) {
        raw = ctx[name.slice(0, -'__missing'.length)] == null ? 1 : 0;
      } else {
        raw = ctx[name] != null && Number.isFinite(Number(ctx[name]))
          ? Number(ctx[name]) : spec.imputerMedian[i];
      }
      return (raw - spec.scalerMean[i]) / (spec.scalerScale[i] || 1);
    });
  }

  function predictLambda(spec, vector) {
    let z = spec.intercept;
    for (let i = 0; i < spec.coefficients.length; i++) z += spec.coefficients[i] * vector[i];
    return Math.min(40, Math.max(0.01, Math.exp(z)));
  }

  const factorial = [1];
  for (let i = 1; i <= 60; i++) factorial[i] = factorial[i - 1] * i;

  function poissonOver(lambda, line) {
    // P(X > line) с целым порогом floor(line); линии оканчиваются на .5.
    const k = Math.floor(line);
    let cumulative = 0;
    for (let i = 0; i <= k; i++) {
      cumulative += Math.exp(-lambda) * Math.pow(lambda, i) / factorial[i];
    }
    return Math.min(1, Math.max(0, 1 - cumulative));
  }

  // NB2-хвост: Var = mean + alpha * mean^2, size r = 1/alpha; рекуррентность
  // PMF совпадает с scipy nbinom(n=r, p=r/(r+mean)) — как в клубной модели.
  function nbOver(lambda, line, alpha) {
    const a = Math.max(alpha || 0.01, 0.0001);
    const r = 1 / a;
    const p0 = r / (r + lambda);
    let pmf = Math.pow(p0, r);
    let cumulative = pmf;
    const k = Math.floor(line);
    for (let i = 1; i <= k; i++) {
      pmf *= (r + i - 1) / i * (1 - p0);
      cumulative += pmf;
    }
    return Math.min(1, Math.max(0, 1 - cumulative));
  }

  function overProbability(spec, lambda, line) {
    return spec && spec.type === 'nb2' ? nbOver(lambda, line, spec.nbAlpha) : poissonOver(lambda, line);
  }

  // Узлы изотонного калибратора: кусочно-линейная интерполяция с зажимом.
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

  function marketPrediction(spec, ctx) {
    const lambda = predictLambda(spec, transform(spec, ctx));
    const calibrators = spec.calibration || null;
    return {
      lambda,
      distribution: spec.type === 'nb2' ? 'nb2' : 'poisson',
      lines: (spec.lines || []).map((line) => {
        let over = overProbability(spec, lambda, line);
        if (calibrators && calibrators[String(line)]) over = calibrate(over, calibrators[String(line)]);
        over = Math.min(1, Math.max(0, over));
        return { line, over, under: 1 - over };
      }),
    };
  }

  function predict(model, leagueName, homeName, awayName) {
    if (!model || model.schemaVersion !== 1 || !model.models) return null;
    const comp = resolveCompetition(model, leagueName);
    if (!comp) return null;
    const home = resolveTeam(model, homeName);
    const away = resolveTeam(model, awayName);
    if (!home || !away || !home.team || !away.team) return null;
    const ctx = buildContext(model, comp, home, away);
    const out = {
      league: leagueName,
      leagueLabel: comp.name,
      competition: comp,
      homeModel: home.team.fdoName || home.team.name,
      awayModel: away.team.fdoName || away.team.name,
      trainingThrough: model.trainingThrough,
    };
    if (model.models.corners) out.corners = marketPrediction(model.models.corners, ctx);
    if (model.models.yellowCards) out.yellowCards = marketPrediction(model.models.yellowCards, ctx);
    return out;
  }

  // Дедуп-ключ: варианты имени одного матча схлопываются в модельную
  // идентичность (турнир + канонические ключи команд); вне покрытия — сырые
  // нормализованные имена.
  function canonicalFixtureKey(model, leagueName, homeName, awayName) {
    const comp = model ? resolveCompetition(model, leagueName) : null;
    if (comp) {
      const home = resolveTeam(model, homeName);
      const away = resolveTeam(model, awayName);
      if (home && away) return `eurostats|${comp.code}|${home.key}|${away.key}`;
    }
    return 'raw|' + norm(homeName) + '|' + norm(awayName);
  }

  return { norm, resolveCompetition, resolveTeam, buildContext, transform,
           predictLambda, poissonOver, nbOver, overProbability, calibrate,
           marketPrediction, predict, canonicalFixtureKey };
}));
