#!/usr/bin/env python3
"""Build the national-teams training dataset from international_results.

Source: martj42/international_results (CC0-1.0) — every men's international
since 1872: date, home/away team, score, tournament, neutral-venue flag.
Domestic-league sources have no national-team history at all, so this dataset
is the entire basis of the NT model (Nations League and other internationals).

The CSV is downloaded by the Retrain workflow into data/raw-nt/ and is never
committed (project rule); this script emits data/nt_training.csv, likewise a
workflow-only artifact — only the exported model JSON is committed.

Feature contract (all computed strictly BEFORE each match — no leakage):
  home_nt_elo / away_nt_elo   — single global Elo over ALL internationals in
                                one chronology (national teams play across
                                confederations, so the scale is comparable;
                                unlike clubs there is no per-country isolation).
                                Home bonus applies only at non-neutral venues;
                                K depends on match weight (friendly 20,
                                competitive 30, tournament finals 40).
  home_gf_decay / home_ga_decay / away_* — exponentially decaying goals
                                for/against per match (half-life 365 days),
                                initialised at the running global mean.
  home_gf_avg5 / home_ga_avg5 / away_* — plain mean over the team's last 5
                                matches (fewer if the team is new; running
                                global mean before the team's first match).
  is_neutral                  — neutral-venue flag of THIS match.
  ctx_is_friendly             — friendlies and friendly-like exhibition
                                tournaments (FIFA Series, King's Cup, ...).
  ctx_is_finals               — final tournaments (World Cup, Euro, Copa
                                América, AFCON, Asian Cup, Gold Cup).
  Reference category: competitive non-finals (Nations League, qualifiers,
  regional cups).

Output rows start at TRAIN_START (the replay from 1872 is burn-in so that
ratings are mature by then); targets are the actual goals.
"""

import argparse
import csv
from collections import deque
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "raw-nt" / "results.csv"
DEFAULT_OUTPUT = ROOT / "data" / "nt_training.csv"

TRAIN_START = date(2022, 1, 1)
ELO_START = 1500.0
ELO_HOME_BONUS = 100.0
ELO_K = {"friendly": 20.0, "finals": 40.0, "competitive": 30.0}
DECAY_HALF_LIFE_DAYS = 365.0
AVG_WINDOW = 5

FINALS_TOURNAMENTS = {
    "FIFA World Cup", "UEFA Euro", "Copa América", "African Cup of Nations",
    "AFC Asian Cup", "Gold Cup",
}
FRIENDLY_TOURNAMENTS = {
    "Friendly", "FIFA Series", "CONCACAF Series", "King's Cup", "Kirin Cup",
    "Kirin Challenge Cup", "China Cup", "Intercontinental Cup",
}

FEATURES = [
    "home_nt_elo", "away_nt_elo",
    "home_gf_decay", "home_ga_decay", "away_gf_decay", "away_ga_decay",
    "home_gf_avg5", "home_ga_avg5", "away_gf_avg5", "away_ga_avg5",
    "is_neutral", "ctx_is_friendly", "ctx_is_finals",
]


def tournament_category(tournament):
    if tournament in FRIENDLY_TOURNAMENTS:
        return "friendly"
    if tournament in FINALS_TOURNAMENTS:
        return "finals"
    return "competitive"


class TeamState:
    """Mutable pre-match state of one national team.

    gf/ga decay values are exponentially weighted MEANS (weight mass tracked
    alongside), so the value stays on the per-match goals scale regardless of
    how often the team plays."""

    def __init__(self):
        self.elo = ELO_START
        self.gf_sum = 0.0  # decayed goals-for mass
        self.ga_sum = 0.0
        self.weight = 0.0  # decayed match-count mass
        self.last_decay_date = None
        self.recent = deque(maxlen=AVG_WINDOW)  # (gf, ga) pairs

    def _decay_factor(self, when):
        if self.last_decay_date is None:
            return 0.0
        age = (when - self.last_decay_date).days
        return 0.5 ** (age / DECAY_HALF_LIFE_DAYS) if age > 0 else 1.0

    def snapshot(self, when, global_mean):
        """Pre-match feature values; falls back to the running global mean
        for teams without history (no NaN ever enters the dataset)."""
        factor = self._decay_factor(when)
        weight = self.weight * factor
        gf_decay = (self.gf_sum * factor) / weight if weight > 0 else None
        ga_decay = (self.ga_sum * factor) / weight if weight > 0 else None
        if self.recent:
            gf5 = sum(g for g, _ in self.recent) / len(self.recent)
            ga5 = sum(a for _, a in self.recent) / len(self.recent)
        else:
            gf5 = ga5 = None
        return {
            "elo": self.elo,
            "gf_decay": gf_decay if gf_decay is not None else global_mean,
            "ga_decay": ga_decay if ga_decay is not None else global_mean,
            "gf_avg5": gf5 if gf5 is not None else global_mean,
            "ga_avg5": ga5 if ga5 is not None else global_mean,
        }

    def update(self, when, gf, ga):
        factor = self._decay_factor(when)
        self.gf_sum = self.gf_sum * factor + gf
        self.ga_sum = self.ga_sum * factor + ga
        self.weight = self.weight * factor + 1.0
        self.last_decay_date = when
        self.recent.append((gf, ga))


def elo_expectation(home_elo, away_elo, neutral):
    bonus = 0.0 if neutral else ELO_HOME_BONUS
    return 1.0 / (1.0 + 10.0 ** (-((home_elo + bonus) - away_elo) / 400.0))


def parse_row(row):
    """One results.csv row -> (date, home, away, hg, ag, tournament, neutral)
    or None for malformed rows."""
    try:
        when = date.fromisoformat(row["date"].strip())
        hg = int(row["home_score"])
        ag = int(row["away_score"])
    except (KeyError, ValueError, AttributeError):
        return None
    home, away = row.get("home_team", "").strip(), row.get("away_team", "").strip()
    if not home or not away or hg < 0 or ag < 0:
        return None
    neutral = row.get("neutral", "").strip().upper() == "TRUE"
    return when, home, away, hg, ag, row.get("tournament", "").strip(), neutral


def replay(rows, collect_from=TRAIN_START):
    """Replay the full chronology once. Returns (feature_rows, states,
    global_mean): feature rows for matches on/after ``collect_from`` (empty
    list when None), the final TeamState per team and the final running
    global mean of goals per team-match."""
    states = {}
    output = []
    goals_seen = 0
    goals_total = 0.0

    def global_mean():
        return goals_total / goals_seen if goals_seen else 1.3

    for when, home, away, hg, ag, tournament, neutral in rows:
        hs = states.setdefault(home, TeamState())
        as_ = states.setdefault(away, TeamState())
        hf = hs.snapshot(when, global_mean())
        af = as_.snapshot(when, global_mean())
        category = tournament_category(tournament)

        if collect_from is not None and when >= collect_from:
            output.append({
                "date": when.isoformat(),
                "home": home, "away": away, "tournament": tournament,
                "home_nt_elo": hf["elo"], "away_nt_elo": af["elo"],
                "home_gf_decay": hf["gf_decay"], "home_ga_decay": hf["ga_decay"],
                "away_gf_decay": af["gf_decay"], "away_ga_decay": af["ga_decay"],
                "home_gf_avg5": hf["gf_avg5"], "home_ga_avg5": hf["ga_avg5"],
                "away_gf_avg5": af["gf_avg5"], "away_ga_avg5": af["ga_avg5"],
                "is_neutral": 1 if neutral else 0,
                "ctx_is_friendly": 1 if category == "friendly" else 0,
                "ctx_is_finals": 1 if category == "finals" else 0,
                "target_home_goals": hg, "target_away_goals": ag,
            })

        # State updates happen strictly after the snapshot (no leakage).
        expected = elo_expectation(hf["elo"], af["elo"], neutral)
        score_h = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
        k = ELO_K[category]
        hs.elo += k * (score_h - expected)
        as_.elo -= k * (score_h - expected)
        hs.update(when, hg, ag)
        as_.update(when, ag, hg)
        goals_seen += 2
        goals_total += hg + ag

    return output, states, global_mean()


def build(rows):
    """Feature rows from TRAIN_START on (ratings mature during the burn-in)."""
    return replay(rows, collect_from=TRAIN_START)[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    with open(args.input, newline="", encoding="utf-8") as handle:
        rows = [parsed for parsed in (parse_row(r) for r in csv.DictReader(handle)) if parsed]
    if not rows:
        raise SystemExit(f"{args.input}: нет валидных строк")
    rows.sort(key=lambda r: r[0])

    dataset = build(rows)
    if len(dataset) < 1000:
        raise SystemExit(f"подозрительно мало обучающих строк: {len(dataset)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["date", "home", "away", "tournament", *FEATURES,
               "target_home_goals", "target_away_goals"]
    with open(args.output, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(dataset)
    print(f"nt dataset: {len(rows)} матчей в реплее, {len(dataset)} строк с {TRAIN_START} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
