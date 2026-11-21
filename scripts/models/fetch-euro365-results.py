#!/usr/bin/env python3
"""Publish normalized final football results for the current Moscow game day.

Only public sporting results are written. Confirmed bets and stake amounts remain
in the owner's browser and are never inputs to this acquisition pipeline.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "fetch_euro365_odds", Path(__file__).with_name("fetch-euro365-odds.py"))
_ODDS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ODDS)
first = _ODDS.first
moscow_day_key = _ODDS.moscow_day_key
event_kickoff = _ODDS.event_kickoff
obj = _ODDS.obj
request_json = _ODDS.request_json
unwrap_data = _ODDS.unwrap_data

SCORES_PATH = "/v1/scores"
FINAL_RE = re.compile(r"(?:^|[^a-z])(?:end|ended|finish|finished|completed|ft|full[ _-]?time)(?:$|[^a-z])", re.I)
POSTPONED_RE = re.compile(r"postpon|перенес", re.I)
CANCELLED_RE = re.compile(r"cancel|отмен", re.I)
ABANDONED_RE = re.compile(r"abandon|прерван", re.I)
FT_TEXT_RE = re.compile(r"(?:ФТ|FT|FULL TIME|FINISHED|COMPLETED)\s*[·:|\-—]?\s*(\d+)\s*[:–-]\s*(\d+)", re.I)
CORNERS_TEXT_RE = re.compile(r"угл\.?\s*:\s*(\d+)", re.I)


def score_entries(payload):
    """Return provider event IDs and score objects without guessing identities."""
    data = unwrap_data(payload)
    if isinstance(data, dict):
        for event_id, score in data.items():
            if isinstance(score, dict):
                yield str(event_id), score
    elif isinstance(data, list):
        for score in data:
            if not isinstance(score, dict):
                continue
            event_id = first(score.get("id"), score.get("eventId"), score.get("event_id"))
            if event_id is not None:
                yield str(event_id), score


def integer_score(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() and number >= 0 else None


def split_score(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+)\s*[:–-]\s*(\d+)\s*", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def structured_score(score):
    nested = obj(score.get("score") or score.get("sc"))
    home = first(score.get("home_score"), score.get("homeScore"), score.get("home"), nested.get("home"))
    away = first(score.get("away_score"), score.get("awayScore"), score.get("away"), nested.get("away"))
    if home is None and away is None:
        pair = split_score(score.get("score"))
        if pair:
            return pair
    home, away = integer_score(home), integer_score(away)
    return (home, away) if home is not None and away is not None else None


def result_status(value, has_score=False):
    text = str(value or "").strip()
    if POSTPONED_RE.search(text):
        return "postponed"
    if CANCELLED_RE.search(text):
        return "cancelled"
    if ABANDONED_RE.search(text):
        return "abandoned"
    if FINAL_RE.search(text) and has_score:
        return "finished"
    return None


def snapshot_result(prediction):
    """Fallback to the already published production snapshot after FT only."""
    text = str(prediction.get("actualResult") or "")
    match = FT_TEXT_RE.search(text)
    if not match:
        return None
    corners = CORNERS_TEXT_RE.search(text)
    return {
        "status": "finished",
        "home_goals": int(match.group(1)),
        "away_goals": int(match.group(2)),
        "total_corners": int(corners.group(1)) if corners else None,
        "evidence": "production_snapshot",
    }


def normalize_result(prediction, score=None):
    status = result_status(score.get("status") if score else None, False)
    if status in {"postponed", "cancelled", "abandoned"}:
        return {"status": status, "home_goals": None, "away_goals": None,
                "total_corners": None, "evidence": "scores_endpoint"}
    goals = structured_score(score) if score else None
    status = result_status(score.get("status") if score else None, goals is not None)
    if status == "finished":
        return {"status": status, "home_goals": goals[0], "away_goals": goals[1],
                "total_corners": integer_score(first(score.get("corners"), score.get("total_corners"))),
                "evidence": "scores_endpoint"}
    return snapshot_result(prediction)


def build_artifact(data, scores_payload, generated_at=None):
    if not isinstance(data, dict) or not isinstance(data.get("predictions"), list):
        raise ValueError("data.json must contain predictions[]")
    day = str(data.get("date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("data.json must contain a valid Moscow date")
    generated = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    scores = dict(score_entries(scores_payload))
    results = []
    for prediction in data["predictions"]:
        if not isinstance(prediction, dict) or prediction.get("source") != "Euro365":
            continue
        event_id = str(prediction.get("eventId") or "").strip()
        kickoff = str(prediction.get("time") or "")
        moment = event_kickoff({"kickoff": kickoff})
        if not event_id or not moment or moscow_day_key(moment) != day:
            continue
        normalized = normalize_result(prediction, scores.get(event_id))
        if not normalized:
            continue
        results.append({
            "event_id": event_id,
            "time": kickoff,
            "league": str(prediction.get("league") or "—"),
            "home": str(prediction.get("home") or ""),
            "away": str(prediction.get("away") or ""),
            "source": "Euro365",
            **normalized,
        })
    results.sort(key=lambda row: (row["time"], row["event_id"]))
    return {"schema_version": 1, "date": day, "generated_at_utc": generated, "results": results}


def write_artifact(output_dir, artifact):
    output_dir.mkdir(parents=True, exist_ok=True)
    day = artifact["date"]
    path = output_dir / f"{day}.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    days = sorted(item.stem for item in output_dir.glob("????-??-??.json"))
    index = {"schema_version": 1, "generated_at_utc": artifact["generated_at_utc"], "days": days}
    (output_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/results"))
    args = parser.parse_args(argv)
    data = json.loads(args.data.read_text(encoding="utf-8"))
    import os
    api_key = os.environ.get("EURO365_API_KEY", "").strip()
    scores_payload = {}
    if api_key:
        try:
            scores_payload = request_json(SCORES_PATH, api_key)
        except RuntimeError as error:
            # The production snapshot remains a bounded final-result fallback.
            print(f"Euro365 scores: {error}; используется только FT из production snapshot", file=sys.stderr)
    artifact = build_artifact(data, scores_payload)
    path = write_artifact(args.output_dir, artifact)
    print(json.dumps({"date": artifact["date"], "results": len(artifact["results"]), "file": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
