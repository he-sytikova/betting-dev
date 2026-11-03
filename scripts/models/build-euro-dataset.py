#!/usr/bin/env python3
"""Сборка обучающего датасета еврокубков (ЛЧ + Лига Европы + Лига Конференций).

Источники:
- матчи еврокубков: football-data.org API v4 (сырые JSON кэшируются локально
  через download-fdo-euro.py; токен только в env FDO_TOKEN, файлы не
  коммитятся). Доступность турниров/сезонов зависит от тарифа: загрузчик
  пропускает недоступное, сборщик работает с тем, что есть;
- домашние признаки команд: data/corners_cards_training.csv (уже построенный
  leakage-safe датасет чемпионатов) — для каждой команды берётся её состояние
  (Elo, затухающие рейтинги атаки/защиты, форма по голам) на день ПОСЛЕДНЕГО
  матча чемпионата строго до даты еврокубкового матча. Это консервативное
  приближение «признаков на дату» без утечки.

Кросс-страновые признаки (сравнимость стран):
- euro_elo: отдельный Elo, ведущийся по матчам ВСЕХ трёх турниров в общей
  хронологии (команда, вылетевшая из ЛЧ в ЛЕ, продолжает со своим рейтингом —
  рейтинг не теряется), старт 1500, K=24 с множителем разницы голов (как в
  домашнем Elo сборщика), между сезонами — регрессия 25% к 1500;
- euro_att/euro_def: затухающие (полураспад 75 дней) рейтинги голов в
  еврокубках относительно среднего еврокубкового;
- comp_is_el / comp_is_ecl: индикаторы турнира матча (ЛЧ — референсная
  категория), чтобы модель могла учесть систематическую разницу уровней.

Команды из стран без чемпионата в наших 24 лигах (Австрия, Украина, Чехия и
т. п.) получают только euro-признаки; домашние признаки остаются пустыми и
помечаются индикаторами пропуска — как в основной модели.

Выход: CSV (локально, не коммитится) + печать отчёта о покрытии.
"""

import argparse
import csv
import json
import math
import unicodedata
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FDO_DIR = Path("/mnt/agents/work/data-fdo")
DEFAULT_DOMESTIC = ROOT / "data" / "corners_cards_training.csv"
DEFAULT_OUTPUT = ROOT / "data" / "euro_cups_training.csv"

ELO_START = 1500.0
ELO_K = 24.0
SEASON_REGRESS = 0.25          # доля возврата к 1500 между сезонами
DECAY_HALF_LIFE_DAYS = 75.0

# Турниры и префиксы файлов загрузчика. ЛЧ — референсная категория в
# comp-признаках (comp_is_el = comp_is_ecl = 0).
COMPETITIONS = {
    "CL": {"prefix": "cl", "name": "UEFA Champions League"},
    "EL": {"prefix": "el", "name": "UEFA Europa League"},
    "ECL": {"prefix": "ecl", "name": "UEFA Europa Conference League"},
}

# Явное сопоставление названий football-data.org -> названий football-data.co.uk
# (как в нашем домашнем датасете). None = страна вне наших 24 лиг, домашних
# признаков нет (и не выдумываем).
FDO_TEAM_ALIASES = {
    "AC Milan": "Milan", "AFC Ajax": "Ajax", "AS Monaco FC": "Monaco",
    "AS Roma": "Roma", "Arsenal FC": "Arsenal", "Aston Villa FC": "Aston Villa",
    "Celtic FC": "Celtic", "Chelsea FC": "Chelsea", "Club Brugge KV": "Club Brugge",
    "FC Barcelona": "Barcelona", "FC Porto": "Porto", "Fenerbahçe SK": "Fenerbahce",
    "Galatasaray SK": "Galatasaray", "Girona FC": "Girona", "Juventus FC": "Juventus",
    "Liverpool FC": "Liverpool", "Newcastle United FC": "Newcastle",
    "Olympique de Marseille": "Marseille", "PAE AEK": "AEK",
    "PAE Olympiakos SFP": "Olympiakos", "RB Leipzig": "RB Leipzig",
    "Racing Club de Lens": "Lens", "Real Madrid CF": "Real Madrid",
    "Royal Antwerp FC": "Antwerp", "SSC Napoli": "Napoli", "Sevilla FC": "Sevilla",
    "VfB Stuttgart": "Stuttgart", "Viking FK": "Viking", "Villarreal CF": "Villarreal",
    "1. FC Union Berlin": "Union Berlin", "Atalanta BC": "Atalanta",
    "Athletic Club": "Ath Bilbao", "Bayer 04 Leverkusen": "Leverkusen",
    "Bologna FC 1909": "Bologna", "Borussia Dortmund": "Dortmund",
    "Club Atlético de Madrid": "Ath Madrid", "Como 1907": "Como",
    "Eintracht Frankfurt": "Ein Frankfurt", "FC Bayern München": "Bayern Munich",
    "FC Internazionale Milano": "Inter", "Feyenoord Rotterdam": "Feyenoord",
    "FK Bodø/Glimt": "Bodo/Glimt", "Lille OSC": "Lille",
    "Manchester City FC": "Man City", "Manchester United FC": "Man United",
    "PSV": "PSV Eindhoven", "Paris Saint-Germain FC": "Paris SG",
    "Real Betis Balompié": "Betis", "Real Sociedad de Fútbol": "Sociedad",
    "Royale Union Saint-Gilloise": "St. Gilloise", "SS Lazio": "Lazio",
    "Sport Lisboa e Benfica": "Benfica", "Sporting Clube de Braga": "Sp Braga",
    "Sporting Clube de Portugal": "Sp Lisbon", "Stade Brestois 29": "Brest",
    "Tottenham Hotspur FC": "Tottenham",
    # ЛЕ/ЛК: команды чемпионатов из нашего покрытия.
    "Rangers FC": "Rangers", "Olympique Lyonnais": "Lyon", "OGC Nice": "Nice",
    "Toulouse FC": "Toulouse", "Stade Rennais FC 1901": "Rennes",
    "VfL Wolfsburg": "Wolfsburg", "SC Freiburg": "Freiburg",
    "TSG 1899 Hoffenheim": "Hoffenheim", "1. FSV Mainz 05": "Mainz",
    "1. FC Heidenheim 1846": "Heidenheim", "ACF Fiorentina": "Fiorentina",
    "AZ Alkmaar": "AZ Alkmaar", "Vitória SC": "Guimaraes",
    "RSC Anderlecht": "Anderlecht", "KRC Genk": "Genk", "KAA Gent": "Gent",
    "Standard Liège": "Standard", "Molde FK": "Molde", "Rosenborg BK": "Rosenborg",
    "SK Brann": "Brann", "PAOK FC": "PAOK", "Panathinaikos FC": "Panathinaikos",
    "Beşiktaş JK": "Besiktas", "Trabzonspor": "Trabzonspor",
    "İstanbul Başakşehir FK": "Buyuksehyr", "Sivasspor": "Sivasspor",
    # Вне покрытия чемпионатами (только euro-признаки):
    "AC Sparta Praha": None, "BSC Young Boys": None, "FC København": None,
    "FC Red Bull Salzburg": None, "FK Crvena Zvezda": None, "FK Kairat": None,
    "FK Shakhtar Donetsk": None, "GNK Dinamo Zagreb": None, "LASK Linz": None,
    "Paphos FC": None, "Qarabağ Ağdam FK": None, "Sabah FK": None,
    "SK Slavia Praha": None, "SK Sturm Graz": None, "ŠK Slovan Bratislava": None,
}

DOMESTIC_FEATURES = ["elo", "att_decay", "def_decay",
                     "goal_for_avg5", "goal_against_avg5",
                     "goal_for_avg10", "goal_against_avg10"]

COMP_FEATURES = ["comp_is_el", "comp_is_ecl"]


def norm_name(value: str) -> str:
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def load_euro_matches(fdo_dir: Path) -> list:
    """Читает все файлы <comp>_<season>.json трёх турниров и возвращает
    завершённые матчи в общей хронологии; у каждого матча — поле comp."""
    matches = []
    for comp, spec in COMPETITIONS.items():
        for path in sorted(fdo_dir.glob(f"{spec['prefix']}_*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for m in data.get("matches", []):
                if m["status"] != "FINISHED":
                    continue
                ft = m["score"]["fullTime"]
                if ft.get("home") is None or ft.get("away") is None:
                    continue
                matches.append({
                    "date": pd.Timestamp(m["utcDate"]).tz_convert(None),
                    "season": m["season"]["startDate"][:4],
                    "comp": comp,
                    "stage": m.get("stage") or "",
                    "home_fdo": m["homeTeam"]["name"],
                    "away_fdo": m["awayTeam"]["name"],
                    "home_goals": int(ft["home"]),
                    "away_goals": int(ft["away"]),
                })
    matches.sort(key=lambda r: r["date"])
    return matches


def domestic_state_series(domestic_csv: Path) -> dict:
    """Для каждой команды — хронологический список (date, state) её признаков
    ПОСЛЕ каждого сыгранного матча чемпионата. Состояние на дату еврокубкового
    матча — последняя запись строго до этой даты (послематчевое состояние
    предыдущего дня = доматчевое для следующего)."""
    df = pd.read_csv(domestic_csv, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].notna()].sort_values("date")
    series = defaultdict(list)
    for row in df.itertuples(index=False):
        d = row.date
        series[norm_name(row.home_team)].append((d, {
            "elo": row.home_elo, "att_decay": row.home_att_decay, "def_decay": row.home_def_decay,
            "goal_for_avg5": row.home_goal_team_for_avg5, "goal_against_avg5": row.home_goal_team_against_avg5,
            "goal_for_avg10": row.home_goal_team_for_avg10, "goal_against_avg10": row.home_goal_team_against_avg10,
        }))
        series[norm_name(row.away_team)].append((d, {
            "elo": row.away_elo, "att_decay": row.away_att_decay, "def_decay": row.away_def_decay,
            "goal_for_avg5": row.away_goal_team_for_avg5, "goal_against_avg5": row.away_goal_team_against_avg5,
            "goal_for_avg10": row.away_goal_team_for_avg10, "goal_against_avg10": row.away_goal_team_against_avg10,
        }))
    return series


def state_before(series_list: list, when: pd.Timestamp):
    """Последнее состояние строго до даты (бинпоиск по отсортированным датам)."""
    import bisect
    dates = [d for d, _ in series_list]
    i = bisect.bisect_left(dates, when)
    return series_list[i - 1][1] if i > 0 else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fdo-dir", type=Path, default=DEFAULT_FDO_DIR)
    parser.add_argument("--domestic", type=Path, default=DEFAULT_DOMESTIC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    matches = load_euro_matches(args.fdo_dir)
    if not matches:
        raise SystemExit(f"No finished euro-cup matches in {args.fdo_dir}; run download-fdo-euro.py first")
    per_comp = {comp: sum(1 for m in matches if m["comp"] == comp) for comp in COMPETITIONS}
    print(f"Euro-cup matches loaded: {len(matches)} {per_comp} "
          f"({min(m['date'] for m in matches).date()} .. {max(m['date'] for m in matches).date()})")

    unknown = sorted(({m["home_fdo"] for m in matches} | {m["away_fdo"] for m in matches})
                     - set(FDO_TEAM_ALIASES))
    if unknown:
        raise SystemExit(f"Unmapped FDO teams (extend FDO_TEAM_ALIASES): {unknown}")

    series = domestic_state_series(args.domestic)
    covered = sum(1 for v in FDO_TEAM_ALIASES.values() if v)
    print(f"FDO teams: {len(FDO_TEAM_ALIASES)}, with domestic coverage: {covered}")

    rows, states, coverage, avg_goals = process_matches(matches, series)
    out = pd.DataFrame(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(f"written {len(out)} rows -> {args.output}")
    print("domestic coverage:", coverage)
    print("euro-cup avg goals/match:", round(avg_goals, 3))


def process_matches(matches, series):
    """Прогоняет матчи всех турниров по общей хронологии: считает признаки без
    утечки и возвращает (rows, final_states, coverage, avg_goals), где
    final_states — euro-состояния команд после последнего матча (для
    рантайм-экспорта). Elo и затухающие рейтинги единые для всех турниров:
    переход команды из ЛЧ в ЛЕ/ЛК рейтинг не обнуляет."""
    # --- Кросс-страновой Elo и затухающие euro-рейтинги по самим еврокубкам ---
    euro_elo = defaultdict(lambda: ELO_START)
    euro_gf = defaultdict(float)   # затухающая сумма забитых
    euro_ga = defaultdict(float)   # затухающая сумма пропущенных
    euro_w = defaultdict(float)    # затухающий вес
    euro_last = {}
    total_goals, total_matches = 0.0, 0
    seen_seasons = set()

    rows, coverage = [], {"both": 0, "home_only": 0, "away_only": 0, "none": 0}
    for m in matches:
        # межсезонная регрессия Elo к среднему
        if m["season"] not in seen_seasons:
            seen_seasons.add(m["season"])
            for t in list(euro_elo):
                euro_elo[t] = ELO_START + (euro_elo[t] - ELO_START) * (1 - SEASON_REGRESS)
            euro_gf.clear(); euro_ga.clear(); euro_w.clear(); euro_last.clear()

        h, a = m["home_fdo"], m["away_fdo"]
        days = lambda t: (m["date"] - euro_last[t]).days if t in euro_last else None

        def euro_rating(store, team):
            d = days(team)
            w_prev = euro_w[team] * (0.5 ** (d / DECAY_HALF_LIFE_DAYS)) if d is not None else 0.0
            s_prev = store[team] * (0.5 ** (d / DECAY_HALF_LIFE_DAYS)) if d is not None else 0.0
            return (s_prev / w_prev) if w_prev > 1e-9 else None

        feat = {
            "date": m["date"].date().isoformat(), "season": m["season"],
            "comp": m["comp"], "stage": m["stage"],
            "home_fdo": h, "away_fdo": a,
            "comp_is_el": 1 if m["comp"] == "EL" else 0,
            "comp_is_ecl": 1 if m["comp"] == "ECL" else 0,
            "home_euro_elo": euro_elo[h], "away_euro_elo": euro_elo[a],
            "home_euro_games": int(round(euro_w[h])) if h in euro_last else 0,
            "away_euro_games": int(round(euro_w[a])) if a in euro_last else 0,
            "home_euro_gf": euro_rating(euro_gf, h), "home_euro_ga": euro_rating(euro_ga, h),
            "away_euro_gf": euro_rating(euro_gf, a), "away_euro_ga": euro_rating(euro_ga, a),
            "target_home_goals": m["home_goals"], "target_away_goals": m["away_goals"],
        }
        # домашние признаки (состояние строго до даты матча)
        for side, fdo in (("home", h), ("away", a)):
            dom_name = FDO_TEAM_ALIASES[fdo]
            st = state_before(series.get(norm_name(dom_name), []), m["date"]) if dom_name else None
            if st:
                for k in DOMESTIC_FEATURES:
                    feat[f"{side}_dom_{k}"] = st[k]
                feat[f"{side}_dom_missing"] = 0
            else:
                for k in DOMESTIC_FEATURES:
                    feat[f"{side}_dom_{k}"] = None
                feat[f"{side}_dom_missing"] = 1
        key = (feat["home_dom_missing"], feat["away_dom_missing"])
        coverage[{ (0,0):"both",(0,1):"home_only",(1,0):"away_only",(1,1):"none" }[key]] += 1

        # обновление euro-истории ПОСЛЕ фиксации признаков (без утечки)
        eh, ea = euro_elo[h], euro_elo[a]
        exp_h = 1.0 / (1.0 + 10 ** (-((eh + 100) - ea) / 400.0))  # +100 — поле
        gd = abs(m["home_goals"] - m["away_goals"])
        mult = 1.0 if gd <= 1 else (1.5 if gd == 2 else (11 + gd) / 8.0)
        delta = ELO_K * mult * ((1.0 if m["home_goals"] > m["away_goals"] else 0.5 if m["home_goals"] == m["away_goals"] else 0.0) - exp_h)
        euro_elo[h] = eh + delta
        euro_elo[a] = ea - delta
        for team, gf, ga in ((h, m["home_goals"], m["away_goals"]), (a, m["away_goals"], m["home_goals"])):
            d = days(team)
            decay = 0.5 ** (d / DECAY_HALF_LIFE_DAYS) if d is not None else 0.0
            euro_gf[team] = euro_gf[team] * decay + gf
            euro_ga[team] = euro_ga[team] * decay + ga
            euro_w[team] = euro_w[team] * decay + 1.0
            euro_last[team] = m["date"]
        total_goals += m["home_goals"] + m["away_goals"]
        total_matches += 1
        rows.append(feat)

    final_states = {
        team: {
            "euroElo": euro_elo[team],
            "euroGf": (euro_gf[team] / euro_w[team]) if euro_w[team] > 1e-9 else None,
            "euroGa": (euro_ga[team] / euro_w[team]) if euro_w[team] > 1e-9 else None,
            "euroGames": sum(1 for m in matches if m["home_fdo"] == team or m["away_fdo"] == team),
            "euroLastDate": euro_last[team].date().isoformat() if team in euro_last else None,
        }
        for team in {m["home_fdo"] for m in matches} | {m["away_fdo"] for m in matches}
    }
    avg_goals = total_goals / total_matches if total_matches else 0.0
    return rows, final_states, coverage, avg_goals


if __name__ == "__main__":
    main()
