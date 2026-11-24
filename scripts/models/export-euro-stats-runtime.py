#!/usr/bin/env python3
"""Экспорт еврокубковых моделей статистики (угловые + ЖК) для браузерного
рантайма.

После валидации (train-euro-stats.py) каждая модель дообучается на ВСЕХ
строках с известной целью (угловые — сезоны 2025+, ЖК — с 2021) с alpha и
recency half-life, выбранными на validation. Экспортируется всё для скоринга
без Python: турниры и варианты названий (гейтинг в JS по списку из JSON),
порядок признаков, медианы импьютера с индикаторными колонками, параметры
скейлера, коэффициенты, тип распределения (Poisson/NB2 + alpha), линии,
изотонные калибраторы (если внедрены), финальные euro-состояния команд
(включая истории угловых/ЖК), снимки домашних признаков и скользящие
средние тоталов по турнирам.

Выход: data/euro_stats_models.json. Сырые данные не коммитятся.
"""

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BSD_DIR = Path("/mnt/agents/work/data-bsd")
DEFAULT_DOMESTIC = ROOT / "data" / "corners_cards_training.csv"
DEFAULT_VALIDATION = ROOT / "data" / "euro_stats_model_validation.json"
DEFAULT_OUTPUT = ROOT / "data/euro_stats_models.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_module("build_euro_dataset", Path(__file__).with_name("build-euro-dataset.py"))
trainer = _load_module("train_euro_stats", Path(__file__).with_name("train-euro-stats.py"))
goals_exporter = _load_module("export_euro_runtime",
                              Path(__file__).with_name("export-euro-runtime.py"))
recency = _load_module("recency", Path(__file__).with_name("recency.py"))

# Ключи рынков в JSON — как у клубного рантайма (internal-model.js), чтобы
# контур дня переиспользовал одинаковую форму prediction[market].lines.
MARKETS = {"corners": "target_corners_total", "yellowCards": "target_cards_total"}
MAX_LAMBDA = 40.0


def _mean_ll(scored: dict) -> float:
    return float(np.mean([x["logLoss"] for x in scored["lines"]]))


def fit_market(df, target, features, alpha, half_life):
    sub = df[pd.to_numeric(df[target], errors="coerce").notna()].copy()
    y = pd.to_numeric(sub[target]).to_numpy(float)
    prep = Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True,
                                              keep_empty_features=True)),
                     ("scale", StandardScaler())])
    x_all = prep.fit_transform(sub[features])
    imputer = prep.named_steps["impute"]
    scaler = prep.named_steps["scale"]
    missing_indices = [int(i) for i in imputer.indicator_.features_]
    expanded = features + [features[i] + "__missing" for i in missing_indices]
    # keep_empty_features=True: statistics_ всегда полной длины; у пустых
    # колонок медиана NaN -> в JSON кладём 0 (в рантайме они приходят как
    # индикатор пропуска, значение не используется).
    medians = [float(v) if np.isfinite(v) else 0.0 for v in imputer.statistics_] \
        + [0.0] * len(missing_indices)
    means = list(map(float, scaler.mean_))
    scales = list(map(float, scaler.scale_))
    w = recency.recency_weights(sub["date"], half_life)
    reg = PoissonRegressor(alpha=alpha, max_iter=800, tol=1e-8).fit(
        x_all, y, sample_weight=w)
    coefs = list(map(float, reg.coef_))
    if not (len(expanded) == len(medians) == len(means) == len(scales) == len(coefs)):
        raise RuntimeError(f"Export dimensions mismatch for {target}")
    return {
        "spec": {
            "features": expanded,
            "baseFeatures": features,
            "imputerMedian": medians,
            "scalerMean": means,
            "scalerScale": scales,
            "coefficients": coefs,
            "intercept": float(reg.intercept_),
        },
        "rows": int(len(sub)),
        "through": str(sub["date"].max().date()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bsd-dir", type=Path, default=DEFAULT_BSD_DIR)
    parser.add_argument("--domestic", type=Path, default=DEFAULT_DOMESTIC)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    validation = json.loads(args.validation.read_text(encoding="utf-8"))

    matches = builder.load_euro_matches(args.bsd_dir)
    series = builder.domestic_state_series(args.domestic)
    rows, states, coverage, avg_goals, stat_context = builder.process_matches(matches, series)
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])

    models = {}
    models_meta = {}
    for market, target in MARKETS.items():
        group = "cards" if market == "yellowCards" else "corners"
        rep = validation["markets"][group]
        alpha = rep["poissonTuning"]["selected"]["alpha"]
        half_life = rep["poissonTuning"]["selected"]["recencyHalfLifeDays"]
        distribution = rep["distributionSelection"]["selected"]
        features = trainer.feature_columns(group)
        fitted = fit_market(df, target, features, alpha, half_life)
        spec = fitted["spec"]
        spec["type"] = "nb2" if distribution == "nb" else "poisson"
        if distribution == "nb":
            spec["nbAlpha"] = float(rep["negativeBinomialAlpha"])
        spec["lines"] = trainer.LINES[group]
        if rep["calibration"]["deployed"]:
            spec["calibration"] = rep["calibration"]["lines"]
        spec["trainingRows"] = fitted["rows"]
        spec["trainingThrough"] = fitted["through"]
        models[market] = spec
        models_meta[market] = {"alpha": alpha, "recencyHalfLifeDays": half_life,
                               "distribution": distribution}

    # Снимки команд: те же ключи/алиасы, что у евро-модели голов, плюс
    # euro-истории угловых/ЖК из финального состояния сборщика.
    fdo_by_domestic = {v: k for k, v in builder.FDO_TEAM_ALIASES.items() if v}
    fdo_to_bsd = {v: k for k, v in builder.BSD_TO_FDO.items()}

    def canonical_of_fdo(fdo_name: str) -> str:
        dom = builder.FDO_TEAM_ALIASES.get(fdo_name)
        if dom:
            return dom
        bsd = fdo_to_bsd.get(fdo_name, fdo_name)
        return builder.canonical_name(bsd) if bsd in builder.BSD_TEAM_ALIASES else fdo_name

    teams = {}
    team_variants = {}
    for bsd_name, dom_name in builder.BSD_TEAM_ALIASES.items():
        canonical = builder.canonical_name(bsd_name)
        key = builder.norm_name(canonical)
        fdo_name = builder.BSD_TO_FDO.get(bsd_name) or fdo_by_domestic.get(dom_name)
        if key not in teams:
            entry = {"name": canonical, "bsdName": bsd_name, "domesticName": dom_name}
            if fdo_name:
                entry["fdoName"] = fdo_name
            st = states.get(canonical)
            if st:
                entry["euro"] = {k: (round(v, 6) if isinstance(v, float) else v)
                                 for k, v in st.items()}
            if dom_name:
                dom_series = series.get(builder.norm_name(dom_name), [])
                if dom_series:
                    last = dom_series[-1][1]
                    entry["domestic"] = {}
                    for k in builder.DOMESTIC_FEATURES:
                        v = last[k]
                        entry["domestic"][k] = (round(float(v), 6)
                                                if v is not None and np.isfinite(float(v)) else None)
            teams[key] = entry
            team_variants[key] = set()
        team_variants[key].add(bsd_name)
        if fdo_name:
            team_variants[key].add(fdo_name)
        if dom_name:
            team_variants[key].add(dom_name)

    for fdo_name, variants in goals_exporter.FEED_VARIANTS.items():
        key = builder.norm_name(canonical_of_fdo(fdo_name))
        if key not in teams:
            continue
        team_variants[key].add(fdo_name)
        if isinstance(variants, str):
            variants = [variants]
        team_variants[key].update(variants)

    aliases = {}
    for key, variants in team_variants.items():
        aliases[key] = key
        for v in variants:
            aliases.setdefault(builder.norm_name(v), key)

    comps_present = sorted({m["comp"] for m in matches})
    competitions = [
        {"code": comp,
         "name": builder.COMPETITIONS[comp]["name"],
         "names": goals_exporter.COMPETITION_FEED_NAMES.get(
             comp, [builder.COMPETITIONS[comp]["name"]])}
        for comp in comps_present
    ]

    comp_averages = {}
    for comp, groups in (stat_context.get("compAverages") or {}).items():
        comp_averages[comp] = {
            group: (round(float(v), 6) if v is not None and np.isfinite(float(v)) else None)
            for group, v in groups.items()
        }

    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "experimental-internal",
        "disclaimer": "Экспериментальные внутренние модели угловых и жёлтых карточек для "
                      "еврокубков (Лига чемпионов, Лига Европы, Лига Конференций). Источник "
                      "статистики — BSD (sports.bzzoiro.com): ЖК с 2021, угловые только с "
                      "сезона 2025/26 (основная сетка; ранние квалификационные раунды без "
                      "угловых), выборка по угловым тонкая. Исследовательская оценка, не "
                      "рекомендация к ставке.",
        "trainingThrough": str(max(df["date"]).date()),
        "competitions": competitions,
        "compAverages": comp_averages,
        "models": models,
        "modelsMeta": models_meta,
        "teams": teams,
        "teamAliases": aliases,
        "validation": {
            "report": "data/euro_stats_model_validation.json",
            "testMeanLogLoss": {
                group: {
                    "baseline": _mean_ll(
                        validation["markets"][group]["test"]["compRollingMeanBaseline"]),
                    "rawSelected": validation["markets"][group]["calibration"]["testMeanLogLossRaw"],
                    "calibrated": validation["markets"][group]["calibration"]["testMeanLogLossCalibrated"],
                } for group in ("corners", "cards")
            },
        },
    }

    def sanitize(obj):
        if isinstance(obj, float) and not np.isfinite(obj):
            return None
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize(v) for v in obj]
        return obj

    args.output.write_text(json.dumps(sanitize(payload), indent=1, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "teams": len(teams),
                      "aliases": len(aliases),
                      "models": {m: {"rows": s["trainingRows"],
                                     "type": s["type"]} for m, s in models.items()}},
                     indent=1))


if __name__ == "__main__":
    main()
