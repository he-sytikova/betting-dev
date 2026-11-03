#!/usr/bin/env python3
"""Экспорт модели Лиги чемпионов для браузерного рантайма.

После валидации (train-euro-cl.py) модель дообучается на ВСЕХ завершённых
матчах ЛЧ с alpha, выбранным на validation. Экспортируется всё, что нужно JS
для скоринга без Python: порядок признаков, медианы импьютера (с индикаторными
колонками), параметры скейлера, коэффициенты, развёрнутый rho Dixon–Coles,
финальные euro-состояния команд (Elo, затухающие рейтинги голов) и снимок
домашних признаков на дату экспорта. Сырые данные не коммитятся — коммитится
только этот JSON.
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
DEFAULT_FDO_DIR = Path("/mnt/agents/work/data-fdo")
DEFAULT_DOMESTIC = ROOT / "data" / "corners_cards_training.csv"
DEFAULT_VALIDATION = ROOT / "data" / "euro_cl_model_validation.json"
DEFAULT_OUTPUT = ROOT / "data" / "euro_cl_model.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_module("build_euro_dataset", Path(__file__).with_name("build-euro-dataset.py"))
euro_trainer = _load_module("train_euro_cl", Path(__file__).with_name("train-euro-cl.py"))
trainer = _load_module("train_goals_markets", Path(__file__).with_name("train-goals-markets.py"))
recency = _load_module("recency", Path(__file__).with_name("recency.py"))

MAX_GOALS = 10
TOTAL_LINES = [1.5, 2.5, 3.5, 4.5]

# Варианты написания из провайдеров фида (BSD, Euro365, FB_DATA) -> ключ
# канонического имени (нормализованное FDO-имя). Ключи уже нормализованы
# builder.norm_name; JS применяет ту же нормализацию.
FEED_VARIANTS = {
    "FK Shakhtar Donetsk": ["Shakhtar Donetsk"],
    "Sporting Clube de Portugal": ["Sporting CP", "Sporting Lisbon"],
    "Sporting Clube de Braga": ["Braga", "SC Braga"],
    "FK Bodø/Glimt": ["Bodø/Glimt", "Bodo-Glimt", "Bodo/Glimt"],
    "Lille OSC": ["LOSC Lille", "Lille"],
    "Club Atlético de Madrid": ["Atlético Madrid", "Atletico Madrid"],
    "FC Bayern München": ["Bayern Munich", "Bayern München"],
    "FC Internazionale Milano": ["Inter", "Internazionale Milano", "Inter Milan"],
    "Paris Saint-Germain FC": ["Paris Saint-Germain", "PSG", "Paris SG"],
    "ŠK Slovan Bratislava": ["Slovan Bratislava"],
    "LASK Linz": ["LASK"],
    "AS Roma": ["Roma"],
    "AC Milan": "Milan",
    "AFC Ajax": ["Ajax"],
    "FC Barcelona": ["Barcelona"],
    "Real Madrid CF": ["Real Madrid"],
    "Manchester United FC": ["Manchester United"],
    "Manchester City FC": ["Manchester City"],
    "Newcastle United FC": ["Newcastle United"],
    "Tottenham Hotspur FC": ["Tottenham Hotspur", "Tottenham"],
    "Feyenoord Rotterdam": ["Feyenoord"],
    "FK Crvena Zvezda": ["Crvena Zvezda", "Red Star Belgrade"],
    "FC København": ["FC Copenhagen", "Copenhagen", "København"],
    "FC Red Bull Salzburg": ["Red Bull Salzburg", "Salzburg"],
    "Royale Union Saint-Gilloise": ["Union Saint-Gilloise", "Union SG", "Union St Gilloise"],
    "SK Slavia Praha": ["Slavia Praha", "Slavia Prague"],
    "AC Sparta Praha": ["Sparta Praha", "Sparta Prague"],
    "SK Sturm Graz": ["Sturm Graz"],
    "GNK Dinamo Zagreb": ["Dinamo Zagreb"],
    "FK Kairat": ["Kairat", "Kairat Almaty"],
    "Qarabağ Ağdam FK": ["Qarabağ", "Qarabag", "Qarabağ FK"],
    "BSC Young Boys": ["Young Boys"],
    "Sport Lisboa e Benfica": ["Benfica"],
    "Galatasaray SK": ["Galatasaray"],
    "Fenerbahçe SK": ["Fenerbahçe", "Fenerbahce"],
    "PAE Olympiakos SFP": ["Olympiacos", "Olympiakos"],
    "PAE AEK": ["AEK", "AEK Athens"],
    "Stade Brestois 29": ["Brest"],
    "Racing Club de Lens": ["Lens", "RC Lens"],
    "AS Monaco FC": ["Monaco", "AS Monaco"],
    "Olympique de Marseille": ["Marseille", "Olympique Marseille"],
    "Bayer 04 Leverkusen": ["Leverkusen", "Bayer Leverkusen"],
    "Borussia Dortmund": ["Dortmund"],
    "1. FC Union Berlin": ["Union Berlin"],
    "Eintracht Frankfurt": ["Eintracht Frankfurt"],
    "VfB Stuttgart": ["Stuttgart"],
    "RB Leipzig": ["RB Leipzig", "Leipzig"],
    "Club Brugge KV": ["Club Brugge", "Club Brugge KV"],
    "Royal Antwerp FC": ["Antwerp", "Royal Antwerp"],
    "Celtic FC": ["Celtic"],
    "Arsenal FC": ["Arsenal"],
    "Chelsea FC": ["Chelsea"],
    "Liverpool FC": ["Liverpool"],
    "Aston Villa FC": ["Aston Villa"],
    "Real Sociedad de Fútbol": ["Real Sociedad"],
    "Real Betis Balompié": ["Real Betis", "Betis"],
    "Athletic Club": ["Athletic Club", "Athletic Bilbao"],
    "Sevilla FC": ["Sevilla"],
    "Villarreal CF": ["Villarreal"],
    "Girona FC": ["Girona"],
    "SSC Napoli": ["Napoli"],
    "Juventus FC": ["Juventus"],
    "Atalanta BC": ["Atalanta"],
    "Bologna FC 1909": ["Bologna"],
    "SS Lazio": ["Lazio"],
    "Como 1907": ["Como"],
    "FC Porto": ["Porto"],
    "PSV": ["PSV", "PSV Eindhoven"],
    "Viking FK": ["Viking"],
    "Paphos FC": ["Paphos"],
    "Sabah FK": ["Sabah"],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fdo-dir", type=Path, default=DEFAULT_FDO_DIR)
    parser.add_argument("--domestic", type=Path, default=DEFAULT_DOMESTIC)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    validation = json.loads(args.validation.read_text(encoding="utf-8"))
    alpha_home = validation["tuning"]["homeGoals"]["regularizationAlpha"]
    alpha_away = validation["tuning"]["awayGoals"]["regularizationAlpha"]
    half_life_home = validation["tuning"]["homeGoals"].get("recencyHalfLifeDays")
    half_life_away = validation["tuning"]["awayGoals"].get("recencyHalfLifeDays")
    dc_rho = validation["dixonColes"]["deployedRho"]

    matches = builder.load_cl_matches(args.fdo_dir)
    series = builder.domestic_state_series(args.domestic)
    rows, states, coverage, avg_goals = builder.process_matches(matches, series)
    df = pd.DataFrame(rows)
    features = euro_trainer.FEATURES

    prep = Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                     ("scale", StandardScaler())])
    x_all = prep.fit_transform(df[features])
    imputer = prep.named_steps["impute"]
    scaler = prep.named_steps["scale"]
    missing_indices = [int(i) for i in imputer.indicator_.features_]
    expanded = features + [features[i] + "__missing" for i in missing_indices]
    medians = list(map(float, imputer.statistics_)) + [0.0] * len(missing_indices)
    means = list(map(float, scaler.mean_))
    scales = list(map(float, scaler.scale_))

    df["date"] = pd.to_datetime(df["date"])
    models = {}
    models_meta = {}
    for target, key, alpha, half_life in (("target_home_goals", "home", alpha_home, half_life_home),
                                          ("target_away_goals", "away", alpha_away, half_life_away)):
        # Тот же затухающий вес, что выбран на validation, привязан к свежей дате.
        w = recency.recency_weights(df["date"], half_life)
        reg = PoissonRegressor(alpha=alpha, max_iter=800, tol=1e-8).fit(
            x_all, df[target].to_numpy(float), sample_weight=w)
        models_meta[key] = {"alpha": alpha, "recencyHalfLifeDays": half_life}
        coefs = list(map(float, reg.coef_))
        if not (len(expanded) == len(medians) == len(means) == len(scales) == len(coefs)):
            raise RuntimeError(f"Export dimensions mismatch for {key}: "
                               f"{len(expanded)}/{len(medians)}/{len(means)}/{len(scales)}/{len(coefs)}")
        models[key] = {"coefficients": coefs, "intercept": float(reg.intercept_)}

    # Снимок домашних признаков на дату экспорта = последнее известное состояние.
    teams = {}
    for fdo_name, dom_name in builder.FDO_TEAM_ALIASES.items():
        key = builder.norm_name(fdo_name)
        entry = {"fdoName": fdo_name, "domesticName": dom_name}
        st = states.get(fdo_name)
        if st:
            entry["euro"] = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in st.items()}
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

    aliases = {}
    for fdo_name in builder.FDO_TEAM_ALIASES:
        key = builder.norm_name(fdo_name)
        aliases[key] = key
        variants = FEED_VARIANTS.get(fdo_name, [])
        if isinstance(variants, str):
            variants = [variants]
        dom_name = builder.FDO_TEAM_ALIASES[fdo_name]
        if dom_name:
            variants = variants + [dom_name]
        for v in variants:
            aliases[builder.norm_name(v)] = key

    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "experimental-internal",
        "disclaimer": "Экспериментальная внутренняя модель Лиги чемпионов. Обучена на 557 матчах ЛЧ 2023–2026 "
                      "(football-data.org). Валидация out-of-sample лучше базового среднего, но выборка тонкая, "
                      "преимущество против рынка не измерялось (нет исторических коэффициентов). "
                      "Исследовательская оценка, не рекомендация к ставке.",
        "trainingMatches": int(len(df)),
        "trainingThrough": str(max(df["date"])),
        "competition": {"code": "CL", "names": ["UEFA Champions League", "Champions League"]},
        "features": expanded,
        "baseFeatures": features,
        "imputerMedian": medians,
        "scalerMean": means,
        "scalerScale": scales,
        "models": models,
        "modelsMeta": models_meta,
        "dcRho": dc_rho,
        "maxGoals": MAX_GOALS,
        "totalLines": TOTAL_LINES,
        "leagueAvgGoals": round(avg_goals, 6),
        "teams": teams,
        "teamAliases": aliases,
        "validation": {
            "report": "data/euro_cl_model_validation.json",
            "test1x2LogLoss": validation["metrics"]["test"]["model1x2LogLoss"],
            "testBaseline1x2LogLoss": validation["metrics"]["test"]["baseline1x2LogLoss"],
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

    args.output.write_text(json.dumps(sanitize(payload), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "teams": len(teams), "aliases": len(aliases),
                      "features": len(expanded), "matches": len(df), "dcRho": dc_rho}, indent=1))


if __name__ == "__main__":
    main()
