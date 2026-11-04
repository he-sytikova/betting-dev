/* Еврокубковая внутренняя модель (Лига чемпионов, Лига Европы, Лига
 * Конференций).
 *
 * Чистый рантайм: вероятности считаются из data/euro_cups_model.json
 * (экспорт scripts/models/export-euro-runtime.py). Импьютинг, скейлинг и
 * коэффициенты воспроизводят sklearn-пайплайн экспортёра; поправка
 * Dixon–Coles применяется через dcRho модели. Гейтинг по турнирам идёт по
 * списку competitions из самого JSON (в JS нет захардкоженных названий);
 * «AFC Champions League», «UEFA Youth League» и прочие турниры вне списка
 * отсекаются. Вероятности не выдумываются: если турнир не из модели или
 * команда неизвестна — возвращается null.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.EuroInternalModel = factory();
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

  // Каталог турниров модели: schemaVersion 2 хранит список competitions,
  // старый schemaVersion 1 — одиночное competition (ЛЧ).
  function competitionCatalog(model) {
    if (!model) return [];
    if (Array.isArray(model.competitions) && model.competitions.length) {
      return model.competitions;
    }
    if (model.competition && model.competition.code) {
      return [{ code: model.competition.code, names: model.competition.names || [] }];
    }
    return [];
  }

  // Сопоставляет название лиги из фида с турниром модели. Возвращает
  // { code, name } или null — вероятности для неизвестного турнира не
  // выдумываются.
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

  function baseFeatureVector(model, home, away, comp) {
    const h = home.team, a = away.team;
    const euroOf = (t) => (t.euro || {});
    const domOf = (t) => (t.domestic || null);
    const hE = euroOf(h), aE = euroOf(a);
    const hD = domOf(h), aD = domOf(a);
    const code = comp ? comp.code : '';
    return {
      home_euro_elo: hE.euroElo != null ? hE.euroElo : null,
      away_euro_elo: aE.euroElo != null ? aE.euroElo : null,
      home_euro_gf: hE.euroGf != null ? hE.euroGf : null,
      home_euro_ga: hE.euroGa != null ? hE.euroGa : null,
      away_euro_gf: aE.euroGf != null ? aE.euroGf : null,
      away_euro_ga: aE.euroGa != null ? aE.euroGa : null,
      home_dom_att_decay: hD ? hD.att_decay : null,
      home_dom_def_decay: hD ? hD.def_decay : null,
      away_dom_att_decay: aD ? aD.att_decay : null,
      away_dom_def_decay: aD ? aD.def_decay : null,
      home_dom_goal_for_avg5: hD ? hD.goal_for_avg5 : null,
      home_dom_goal_against_avg5: hD ? hD.goal_against_avg5 : null,
      away_dom_goal_for_avg5: aD ? aD.goal_for_avg5 : null,
      away_dom_goal_against_avg5: aD ? aD.goal_against_avg5 : null,
      home_dom_missing: hD ? 0 : 1,
      away_dom_missing: aD ? 0 : 1,
      comp_is_el: code === 'EL' ? 1 : 0,
      comp_is_ecl: code === 'ECL' ? 1 : 0,
    };
  }

  // Воспроизводит SimpleImputer(median, add_indicator) + StandardScaler
  // по экспортированным параметрам.
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
    const base = baseFeatureVector(model, home, away, comp);
    const vector = transform(model, base);
    const homeLambda = predictLambda(model, vector, 'home');
    const awayLambda = predictLambda(model, vector, 'away');
    const markets = deriveMarkets(homeLambda, awayLambda, model);
    return {
      league: leagueName,
      competition: comp,
      // Отображаемое имя: fdoName есть не у всех (euro-only команды из BSD
      // его не имеют) — откатываемся на каноническое имя.
      home: home.team.fdoName || home.team.name,
      away: away.team.fdoName || away.team.name,
      homeLambda,
      awayLambda,
      markets,
    };
  }

  return { norm, competitionCatalog, resolveCompetition, resolveTeam,
           baseFeatureVector, transform, predictLambda, dcTau, deriveMarkets, predict };
}));
