#!/usr/bin/env python3
"""Read-only preflight audit for experimental model data.

This deliberately does not train models or approve a data source. The current
repository contains aggregated team profiles, not match-level historical rows;
therefore the report remains blocked until a documented raw-history dataset
and source permissions are supplied.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORT_PATH = DATA / "historical_data_audit.json"
RAW_HISTORY_CANDIDATES = (
    DATA / "historical_matches.csv",
    DATA / "historical_matches.json",
    DATA / "historical_matches.parquet",
)

GOAL_REQUIRED = (
    "goal_team_for_avg5", "goal_team_against_avg5",
    "goal_team_for_avg10", "goal_team_against_avg10",
)
CORNERS_CARDS_REQUIRED = (
    "team_for_avg5", "team_against_avg5", "team_for_avg10", "team_against_avg10",
    "card_team_for_avg5", "card_team_against_avg5",
    "card_team_for_avg10", "card_team_against_avg10",
)


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def audit_model(path: Path, required_fields: tuple[str, ...], *, has_goal_models: bool):
    model = load_json(path)
    teams_by_league = model.get("teams") or {}
    league_stats = model.get("leagueStats") or {}
    declared_names = model.get("leagueNames") or {}
    declared_aliases = model.get("leagueAliases") or {}

    league_rows = []
    total_teams = 0
    incomplete_teams = 0
    low_history_teams = 0
    for league_code, teams in sorted(teams_by_league.items()):
        teams = teams or {}
        total_teams += len(teams)
        bad = 0
        low_history = 0
        for team in teams.values():
            stats = (team or {}).get("stats") or {}
            if any(field not in stats or not finite(stats[field]) for field in required_fields):
                bad += 1
            if int((team or {}).get("matchesBefore") or 0) < 10:
                low_history += 1
        incomplete_teams += bad
        low_history_teams += low_history
        league_rows.append({
            "leagueCode": league_code,
            "leagueName": declared_names.get(league_code),
            "teams": len(teams),
            "hasLeagueStats": league_code in league_stats,
            "incompleteTeamProfiles": bad,
            "teamsBelow10Matches": low_history,
        })

    actual_leagues = set(teams_by_league)
    declared_leagues = set(declared_names)
    league_stats_codes = set(league_stats)
    missing_league_stats = sorted(actual_leagues - league_stats_codes)
    declared_without_teams = sorted(declared_leagues - actual_leagues)
    teams_without_names = sorted(actual_leagues - declared_leagues)

    return {
        "file": str(path.relative_to(ROOT)),
        "generatedAt": model.get("generatedAt"),
        "trainingThrough": model.get("trainingThrough"),
        "trainingRows": model.get("trainingRows"),
        "modelTypes": {
            key: value.get("type")
            for key, value in (model.get("models") or {}).items()
            if isinstance(value, dict)
        },
        "actualLeagueCount": len(actual_leagues),
        "declaredLeagueCount": len(declared_leagues),
        "teamCount": total_teams,
        "incompleteTeamProfiles": incomplete_teams,
        "teamsBelow10Matches": low_history_teams,
        "missingLeagueStats": missing_league_stats,
        "declaredLeaguesWithoutTeamProfiles": declared_without_teams,
        "teamLeaguesWithoutDeclaredNames": teams_without_names,
        "leagueAudit": league_rows,
        "hasRequiredModelObjects": bool(model.get("models")) if has_goal_models else bool(model.get("models")),
    }


def main():
    goals_path = DATA / "goals_markets_models.json"
    corners_path = DATA / "corners_cards_models.json"
    source_path = DATA / "source_eligibility.json"
    fixture_path = ROOT / "data.json"

    goals = audit_model(goals_path, GOAL_REQUIRED, has_goal_models=True)
    corners = audit_model(corners_path, CORNERS_CARDS_REQUIRED, has_goal_models=False)

    fixture_summary = {"available": False}
    if fixture_path.exists():
        fixtures = load_json(fixture_path)
        predictions = fixtures.get("predictions") or []
        by_source = Counter(str(row.get("source") or "unknown") for row in predictions)
        fixture_summary = {
            "available": True,
            "generatedAt": fixtures.get("generatedAt"),
            "predictionRows": len(predictions),
            "rowsBySource": dict(sorted(by_source.items())),
            "note": "Current fixture feed only; it is not a historical training dataset.",
        }

    source_eligibility = load_json(source_path) if source_path.exists() else {
        "sources": [],
        "note": "No source eligibility register found.",
    }
    source_rows = source_eligibility.get("sources") or []
    non_approved = [
        row.get("id", "unknown") for row in source_rows
        if row.get("trainingUse") != "approved"
    ]

    raw_history = next((path for path in RAW_HISTORY_CANDIDATES if path.exists()), None)
    blockers = []
    if raw_history is None:
        blockers.append("No match-level historical dataset is present in data/.")
    if non_approved:
        blockers.append("Training permissions are not approved for: " + ", ".join(non_approved))
    if goals["incompleteTeamProfiles"] or corners["incompleteTeamProfiles"]:
        blockers.append("One or more aggregated team profiles are incomplete.")
    if goals["missingLeagueStats"] or corners["missingLeagueStats"]:
        blockers.append("One or more team-profile leagues lack league-level statistics.")

    report = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_deeper_audit" if not blockers else "blocked",
        "readyForTraining": False,
        "trainingWasRun": False,
        "blockers": blockers,
        "rawHistoricalDataset": {
            "found": raw_history is not None,
            "path": str(raw_history.relative_to(ROOT)) if raw_history else None,
            "supportedCandidates": [str(path.relative_to(ROOT)) for path in RAW_HISTORY_CANDIDATES],
        },
        "goalsModel": goals,
        "cornersCardsModel": corners,
        "currentFixtureFeed": fixture_summary,
        "sourceEligibility": source_eligibility,
        "interpretation": [
            "Aggregated team profiles cannot prove season coverage, match-level deduplication, chronological integrity, or source provenance.",
            "A minimum of 10 matches in a team profile is a profile guard only; it is not a sufficient historical-data quality certificate.",
            "No training is permitted until raw match-level history and source-specific training rights are documented and approved.",
        ],
    }

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "readyForTraining": report["readyForTraining"],
        "blockers": blockers,
        "goalsLeagues": goals["actualLeagueCount"],
        "cornersCardsLeagues": corners["actualLeagueCount"],
        "goalsTeams": goals["teamCount"],
        "cornersCardsTeams": corners["teamCount"],
        "report": str(REPORT_PATH.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
