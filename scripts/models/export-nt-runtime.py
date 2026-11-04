#!/usr/bin/env python3
"""Экспорт модели сборных для браузерного рантайма.

После валидации (train-nt.py) модель дообучается на ВСЕХ строках датасета
(2022+) с alpha и полураспадом, выбранными на validation. Экспортируется всё,
что нужно JS для скоринга без Python: список турниров гейта и вариантов их
названий (в JS нет захардкоженных названий), порядок признаков, медианы
импьютера, параметры скейлера, коэффициенты, развёрнутый rho Dixon–Coles и
финальные состояния сборных (Elo, затухающие рейтинги голов, форма avg5).

Команды экспортируются только если играли хотя бы NT_MIN_MATCHES матчей за
окно датасета — иначе их состояние слишком сырым остаётся. Сырые данные
(CSV) не коммитятся — коммитится только этот JSON (data/nt_model.json).
"""

import argparse
import csv
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
DEFAULT_INPUT = ROOT / "data" / "raw-nt" / "results.csv"
DEFAULT_VALIDATION = ROOT / "data" / "nt_model_validation.json"
DEFAULT_OUTPUT = ROOT / "data" / "nt_model.json"

NT_MIN_MATCHES = 8
MAX_GOALS = 10
TOTAL_LINES = [1.5, 2.5, 3.5, 4.5]

# Турнирный гейт страницы: только Лига наций УЕФА (по решению владельца —
# как еврокубки, прочие международные турниры пока не показываем). Варианты
# названий из провайдеров фида; «CONCACAF Nations League» и женская Лига
# наций сюда не входят и отсекаются нормализацией (norm различает их).
COMPETITION_FEED_NAMES = {
    "UNL": ["UEFA Nations League", "Nations League", "UEFA Nations League A",
            "UEFA Nations League B", "UEFA Nations League C", "UEFA Nations League D"],
}

# Каноническое имя international_results -> варианты написания из
# провайдеров фида. Нормализация — JS norm (NFKD, диакритика срезается).
FEED_VARIANTS = {
    "Turkey": ["Türkiye"],
    "Czech Republic": ["Czechia"],
    "Cape Verde": ["Cabo Verde"],
    "Ivory Coast": ["Côte d'Ivoire", "Cote d'Ivoire"],
    "United States": ["USA", "United States of America"],
    "South Korea": ["Korea Republic"],
    "North Korea": ["Korea DPR", "DPR Korea"],
    "China": ["China PR"],
    "Hong Kong": ["Hong Kong China"],
    "DR Congo": ["Congo DR", "Democratic Republic of the Congo", "Congo Kinshasa"],
    "Republic of Ireland": ["Ireland"],
    "North Macedonia": ["Macedonia", "FYR Macedonia"],
    "Bosnia and Herzegovina": ["Bosnia", "Bosnia-Herzegovina", "Bosnia Herzegovina"],
    "Kyrgyzstan": ["Kyrgyz Republic"],
    "Taiwan": ["Chinese Taipei"],
    "Timor-Leste": ["East Timor"],
    "Curaçao": ["Curacao"],
    "São Tomé and Príncipe": ["Sao Tome and Principe"],
    "Moldova": ["Moldavia"],
    "Russia": ["Russian Federation"],
    "Brunei": ["Brunei Darussalam"],
    "Syria": ["Syrian Arab Republic"],
    "Iran": ["IR Iran", "Islamic Republic of Iran"],
    "Egypt": ["Arab Republic of Egypt"],
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_module("build_nt_dataset", Path(__file__).with_name("build-nt-dataset.py"))
recency = _load_module("recency", Path(__file__).with_name("recency.py"))


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        rows = [p for p in (builder.parse_row(r) for r in csv.DictReader(handle)) if p]
    if not rows:
        raise SystemExit(f"{path}: нет валидных строк")
    rows.sort(key=lambda r: r[0])
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    report = json.loads(args.validation.read_text(encoding="utf-8"))
    rows = load_rows(args.input)

    dataset, states, global_mean = builder.replay(rows, collect_from=builder.TRAIN_START)
    df = pd.DataFrame(dataset)
    df["date"] = pd.to_datetime(df["date"])
    if len(df) < 1000:
        raise SystemExit(f"слишком мало строк для экспорта: {len(df)}")

    features = builder.FEATURES
    prep = Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                     ("scale", StandardScaler())])
    x_all = prep.fit_transform(df[features])

    fitted = {}
    for target, key in (("target_home_goals", "home"), ("target_away_goals", "away")):
        tuned = report["tuning"][f"{key}Goals"]
        alpha = tuned["regularizationAlpha"]
        half_life = tuned["recencyHalfLifeDays"]
        w = recency.recency_weights(df["date"], half_life, reference=df["date"].max()) \
            if half_life is not None else None
        reg = PoissonRegressor(alpha=alpha, max_iter=800, tol=1e-8).fit(
            x_all, df[target].to_numpy(float), sample_weight=w)
        fitted[key] = reg

    # Импьютер без индикаторных колонок: NaN в датасете не бывает по
    # контракту билдера, но медианы экспортируем — JS-трансформ симметричен.
    imputer = prep.named_steps["impute"]
    scaler = prep.named_steps["scale"]
    n_base = len(features)
    team_entries = {}
    counts = df["home"].value_counts().add(df["away"].value_counts(), fill_value=0)
    for name, state in states.items():
        if counts.get(name, 0) < NT_MIN_MATCHES:
            continue
        snap = state.snapshot(rows[-1][0], global_mean)
        teams_key = js_norm(name)
        if not teams_key:
            continue
        team_entries[teams_key] = {
            "name": name,
            "ntElo": round(snap["elo"], 2),
            "gfDecay": round(snap["gf_decay"], 4),
            "gaDecay": round(snap["ga_decay"], 4),
            "gfAvg5": round(snap["gf_avg5"], 4),
            "gaAvg5": round(snap["ga_avg5"], 4),
            "matches": int(counts.get(name, 0)),
        }

    aliases = {}
    for key, entry in team_entries.items():
        aliases[js_norm(entry["name"])] = key
    for canonical, variants in FEED_VARIANTS.items():
        key = js_norm(canonical)
        if key not in team_entries:
            continue
        for variant in variants:
            aliases[js_norm(variant)] = key

    model = {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "trainingThrough": df["date"].max().date().isoformat(),
        "trainingMatches": int(len(df)),
        "dataset": "martj42/international_results (CC0-1.0)",
        "competitions": [
            {"code": code, "name": code, "names": names}
            for code, names in COMPETITION_FEED_NAMES.items()
        ],
        "features": features,
        "imputerMedian": [float(v) for v in imputer.statistics_[:n_base]],
        "scalerMean": [float(v) for v in scaler.mean_[:n_base]],
        "scalerScale": [float(v) for v in scaler.scale_[:n_base]],
        "models": {side: {"type": "poisson",
                          "coefficients": [float(c) for c in reg.coef_[:n_base]],
                          "intercept": float(reg.intercept_)}
                   for side, reg in fitted.items()},
        "dcRho": report["dixonColes"]["deployedRho"],
        "maxGoals": MAX_GOALS,
        "totalLines": TOTAL_LINES,
        "teams": team_entries,
        "teamAliases": aliases,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(model, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"nt model: команд={len(team_entries)}, обучающих строк={len(df)}, "
          f"по {model['trainingThrough']}, dcRho={model['dcRho']} -> {args.output}")
    return 0


def js_norm(value):
    """Mirror of norm() in nt-internal-model.js: NFKD, strip diacritics,
    lowercase, non-alnum runs -> single space."""
    import re
    import unicodedata
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return value.strip()


if __name__ == "__main__":
    raise SystemExit(main())
