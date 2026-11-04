/* Модель сборных (Лига наций УЕФА).
 *
 * Чистый рантайм: вероятности считаются из data/nt_model.json (экспорт
 * scripts/models/export-nt-runtime.py). Импьютинг, скейлинг и коэффициенты
 * воспроизводят sklearn-пайплайн экспортёра; поправка Dixon–Coles применяется
 * через dcRho модели. Гейтинг по турнирам идёт по списку competitions из
 * самого JSON (сейчас там только Лига наций УЕФА; «CONCACAF Nations League»
 * и женская ЛН отсекаются нормализацией). Вероятности не выдумываются: если
 * турнир не из модели или сборная неизвестна — возвращается null.
 *
 * Контекст матча при скоринге фиксирован под Лигу наций: официальный
 * соревновательный матч (не товарищеский, не финальный турнир), не нейтральное
 * поле — ЛН играется дома/в гостях. Если когда-нибудь гейт расширят на
 * нейтральные турниры, контекст надо будет пробрасывать параметром.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.NtInternalModel = factory();
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
    if (!model) return [];
    return Array.isArray(model.competitions) ? model.competitions : [];
  }

  // Сопоставляет название лиги из фида с турниром модели; null — вероятности
  // для неизвестного турнира не выдумываются.
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
    const key = model.teamAliases[norm(name)];
    return key ? { key, team: model.teams[key] } : null;
  }

  function baseFeatureVector(model, home, away) {
    const h = home.team, a = away.team;
    return {
      home_nt_elo: h.ntElo,
      away_nt_elo: a.ntElo,
      home_gf_decay: h.gfDecay,
      home_ga_decay: h.gaDecay,
      away_gf_decay: a.gfDecay,
      away_ga_decay: a.gaDecay,
      home_gf_avg5: h.gfAvg5,
      home_ga_avg5: h.gaAvg5,
      away_gf_avg5: a.gfAvg5,
      away_ga_avg5: a.gaAvg5,
      is_neutral: 0,
      ctx_is_friendly: 0,
      ctx_is_finals: 0,
    };
  }

  // Воспроизводит SimpleImputer(median) + StandardScaler по экспортированным
  // параметрам (суффикс __missing поддержан для симметрии с евро-рантаймом,
  // хотя в датасете сборных NaN не бывает по контракту билдера).
  function transform(model, base) {
    return model.features.map((name, i) => {
      let raw;
      if (name.endsWith('__missing')) {
        raw = base[name.slice(0, -'__missing'.length)] == null ? 1 : 0;
      } else {
        raw = base[name] != null ? base[name] : model.imputerMedian[i];
      }
      return (raw - model.scalerMean[i]) / model.scalerScale[i];
    });
  }

  function predictLambda(model, vector, side) {
    const spec = model.models[side];
    let z = spec.intercept;
    for (let i = 0; i < spec.coefficients.length; i++) z += spec.coefficients[i] * vector[i];
    return Math.max(Math.exp(z), 0.01);
  }

  function poissonPmf(k, lambda) {
    let p = Math.exp(-lambda);
    for (let i = 1; i <= k; i++) p *= lambda / i;
    return p;
  }

  function dcTau(x, y, homeLambda, awayLambda, rho) {
    if (x === 0 && y === 0) return 1 - homeLambda * awayLambda * rho;
    if (x === 0 && y === 1) return 1 + homeLambda * rho;
    if (x === 1 && y === 0) return 1 + awayLambda * rho;
    if (x === 1 && y === 1) return 1 - rho;
    return 1;
  }

  function deriveMarkets(homeLambda, awayLambda, model) {
    const maxGoals = model.maxGoals || 10;
    const rho = model.dcRho || 0;
    let p1 = 0, px = 0, p2 = 0;
    const totals = {};
    (model.totalLines || [1.5, 2.5, 3.5, 4.5]).forEach((line) => {
      totals[line] = { under: 0, over: 0 };
    });
    for (let i = 0; i <= maxGoals; i++) {
      for (let j = 0; j <= maxGoals; j++) {
        let p = poissonPmf(i, homeLambda) * poissonPmf(j, awayLambda);
        if (rho && i <= 1 && j <= 1) p *= Math.max(dcTau(i, j, homeLambda, awayLambda, rho), 1e-12);
        if (i > j) p1 += p; else if (i === j) px += p; else p2 += p;
        Object.keys(totals).forEach((line) => {
          if (i + j > Number(line)) totals[line].over += p; else totals[line].under += p;
        });
      }
    }
    const sum = p1 + px + p2;
    p1 /= sum; px /= sum; p2 /= sum;
    Object.keys(totals).forEach((line) => {
      totals[line].over /= sum;
      totals[line].under /= sum;
    });
    return { p1, x: px, p2, dc1x: p1 + px, dc12: p1 + p2, dcx2: px + p2, totals };
  }

  function predict(model, leagueName, homeName, awayName) {
    if (!model) return null;
    const comp = resolveCompetition(model, leagueName);
    if (!comp) return null;
    const home = resolveTeam(model, homeName);
    const away = resolveTeam(model, awayName);
    if (!home || !away) return null;
    const base = baseFeatureVector(model, home, away);
    const vector = transform(model, base);
    const homeLambda = predictLambda(model, vector, 'home');
    const awayLambda = predictLambda(model, vector, 'away');
    const markets = deriveMarkets(homeLambda, awayLambda, model);
    return {
      league: leagueName,
      competition: comp,
      home: home.team.name,
      away: away.team.name,
      homeLambda,
      awayLambda,
      markets,
    };
  }

  return { norm, competitionCatalog, resolveCompetition, resolveTeam,
           baseFeatureVector, transform, predictLambda, dcTau, deriveMarkets, predict };
}));
