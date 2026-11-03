#!/usr/bin/env python3
"""Экспорт еврокубковой модели (ЛЧ + ЛЕ + ЛК) для браузерного рантайма.

После валидации (train-euro-cups.py) модель дообучается на ВСЕХ завершённых
матчах еврокубков с alpha, выбранным на validation. Экспортируется всё, что
нужно JS для скоринга без Python: список турниров и вариантов их названий
(гейтинг в JS идёт по этому списку, захардкоженных списков в JS нет), порядок
признаков, медианы импьютера (с индикаторными колонками), параметры скейлера,
коэффициенты, развёрнутый rho Dixon–Coles, финальные euro-состояния команд
(Elo, затухающие рейтинги голов) и снимок домашних признаков на дату экспорта.
Сырые данные не коммитятся — коммитится только этот JSON.
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
DEFAULT_VALIDATION = ROOT / "data" / "euro_cups_model_validation.json"
DEFAULT_OUTPUT = ROOT / "data" / "euro_cups_model.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_module("build_euro_dataset", Path(__file__).with_name("build-euro-dataset.py"))
euro_trainer = _load_module("train_euro_cups", Path(__file__).with_name("train-euro-cups.py"))
trainer = _load_module("train_goals_markets", Path(__file__).with_name("train-goals-markets.py"))
recency = _load_module("recency", Path(__file__).with_name("recency.py"))

MAX_GOALS = 10
TOTAL_LINES = [1.5, 2.5, 3.5, 4.5]

# Варианты написания ТУРНИРОВ из провайдеров фида -> код турнира. «AFC
# Champions League» и «UEFA Youth League» сюда не входят и отсекаются гейтом.
COMPETITION_FEED_NAMES = {
    "CL": ["UEFA Champions League", "Champions League"],
    "EL": ["UEFA Europa League", "Europa League"],
    "ECL": ["UEFA Europa Conference League", "Europa Conference League",
            "UEFA Conference League", "Conference League"],
}

# Варианты написания КОМАНД из провайдеров фида (BSD, Euro365, FB_DATA) -> ключ
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
    # ЛЕ/ЛК: варианты названий команд из фидов.
    "Rangers FC": ["Rangers"],
    "Olympique Lyonnais": ["Lyon", "Olympique Lyon"],
    "OGC Nice": ["Nice"],
    "Toulouse FC": ["Toulouse"],
    "Stade Rennais FC 1901": ["Rennes", "Stade Rennais"],
    "VfL Wolfsburg": ["Wolfsburg"],
    "SC Freiburg": ["Freiburg"],
    "TSG 1899 Hoffenheim": ["Hoffenheim", "1899 Hoffenheim"],
    "1. FSV Mainz 05": ["Mainz", "Mainz 05"],
    "1. FC Heidenheim 1846": ["Heidenheim", "FC Heidenheim"],
    "ACF Fiorentina": ["Fiorentina"],
    "AZ Alkmaar": ["AZ"],
    "Vitória SC": ["Vitória Guimarães", "Vitoria Guimaraes", "Guimaraes"],
    "RSC Anderlecht": ["Anderlecht"],
    "KRC Genk": ["Genk"],
    "KAA Gent": ["Gent", "AA Gent"],
    "Standard Liège": ["Standard Liege", "Standard"],
    "Molde FK": ["Molde"],
    "Rosenborg BK": ["Rosenborg"],
    "SK Brann": ["Brann"],
    "PAOK FC": ["PAOK", "PAOK Salonika"],
    "Panathinaikos FC": ["Panathinaikos"],
    "Beşiktaş JK": ["Beşiktaş", "Besiktas"],
    "Trabzonspor": ["Trabzonspor"],
    "İstanbul Başakşehir FK": ["İstanbul Başakşehir", "Istanbul Basaksehir",
                               "Başakşehir", "Basaksehir"],
    "Sivasspor": ["Sivasspor"],
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

    matches = builder.load_euro_matches(args.fdo_dir)
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

    comps_present = sorted({m["comp"] for m in matches})
    competitions = [
        {"code": comp,
         "name": builder.COMPETITIONS[comp]["name"],
         "names": COMPETITION_FEED_NAMES.get(comp, [builder.COMPETITIONS[comp]["name"]])}
        for comp in comps_present
    ]
    per_comp = {comp: sum(1 for m in matches if m["comp"] == comp) for comp in comps_present}

    payload = {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "experimental-internal",
        "disclaimer": "Экспериментальная внутренняя модель еврокубков (Лига чемпионов, Лига Европы, "
                      "Лига Конференций). Обучена на матчах football-data.org 2023–2026; единый "
                      "кросс-страновой Elo и форма ведутся по всем турнирам в общей хронологии. "
                      "Валидация out-of-sample лучше базового среднего, но проверки против рынка не было "
                      "(нет исторических коэффициентов), выборка по ЛЕ/ЛК особенно тонкая. "
                      "Исследовательская оценка, не рекомендация к ставке.",
        "trainingMatches": int(len(df)),
        "trainingMatchesByCompetition": per_comp,
        "trainingThrough": str(max(df["date"])),
        "competitions": competitions,
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
            "report": "data/euro_cups_model_validation.json",
            "test1x2LogLoss": validation["metrics"]["test"]["model1x2LogLoss"],
            "testBaseline1x2LogLoss": validation["metrics"]["test"]["baseline1x2LogLoss"],
            "testByCompetition": validation["metrics"]["test"].get("byCompetition", {}),
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
                      "features": len(expanded), "matches": len(df),
                      "competitions": comps_present, "dcRho": dc_rho}, indent=1))


if __name__ == "__main__":
    main()
