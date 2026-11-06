#!/usr/bin/env python3
"""Fetch tomorrow's (Moscow day) fixture list from the Euro365 prematch feed.

The dev site shows model forecasts for the next game day on
tomorrow-model.html. The production snapshot (data.json) only covers the
current day, so the fixture list for tomorrow comes straight from the Euro365
prematch endpoint the dev pipeline already polls for odds — but WITHOUT any
odds: this script never calls /v1/odds, it writes only eventId, kickoff time,
league and team names to data/fixtures-tomorrow.json.

Scope per the source-eligibility registry: Euro365 data is licensed for
display/transformation during an active subscription. This artifact is used
only to display model forecasts on the dev pages — never for model training,
never redistributed as a raw feed.

Without EURO365_API_KEY the script exits 0 without writing anything, so the
refresh workflow keeps working and the tomorrow page shows its empty state.
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = "https://api.euro365.bet"
MOSCOW_TZ = timezone(timedelta(hours=3))  # Europe/Moscow has no DST since 2014.
MAX_ATTEMPTS = 4


def obj(value):
    return value if isinstance(value, dict) else {}


def unwrap_data(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def event_entries(payload):
    """Yield (event_id, event) from either a list or a dict payload."""
    data = unwrap_data(payload)
    if isinstance(data, list):
        for index, event in enumerate(data):
            if isinstance(event, dict):
                yield str(event.get("id") or event.get("event_id") or index), event
    elif isinstance(data, dict):
        for key, event in data.items():
            if isinstance(event, dict):
                yield str(key), event


def first(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def event_kickoff(event):
    raw = first(event.get("kickoff"), event.get("start_time"), event.get("startTime"),
                event.get("date"), event.get("ts"))
    if raw is None:
        return None
    try:
        numeric = float(raw)
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None:
        if numeric < 100000000000:
            numeric *= 1000
        return datetime.fromtimestamp(numeric / 1000, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def moscow_day_key(moment):
    return moment.astimezone(MOSCOW_TZ).strftime("%Y-%m-%d")


def event_league(event):
    tournament = first(event.get("tournament"), event.get("league"), event.get("competition"))
    if isinstance(tournament, str):
        return tournament
    if isinstance(event.get("t"), list) and len(event["t"]) > 1:
        return str(event["t"][1])
    if isinstance(tournament, dict):
        return str(first(tournament.get("name"), tournament.get("title"),
                         event.get("league_name"), event.get("tournament_name"), "—"))
    return str(first(event.get("league_name"), event.get("tournament_name"), "—"))


def event_home_away(event):
    home = first(event.get("h"), event.get("home"), event.get("home_name"),
                 event.get("homeTeam"), event.get("home_team"), "—")
    away = first(event.get("a"), event.get("away"), event.get("away_name"),
                 event.get("awayTeam"), event.get("away_team"), "—")
    return str(home), str(away)


def request_json(path, api_key, timeout=10):
    """GET with Euro365 rate-limit handling: retry 429 with Retry-After or
    bounded backoff, sequential requests only (the feed is polled politely)."""
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(
            BASE + path,
            headers={"X-API-Key": api_key, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[:160]
            if error.code == 429 and attempt < MAX_ATTEMPTS - 1:
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    wait = min(max(float(retry_after), 0.0), 60.0) if retry_after else min(2.0 * (2 ** attempt), 15.0)
                except (TypeError, ValueError):
                    wait = min(2.0 * (2 ** attempt), 15.0)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Euro365 HTTP {error.code}: {body}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(0.7)
                continue
            raise RuntimeError(f"Euro365: сбой запроса {path}: {error}")
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RuntimeError(f"Euro365: {payload.get('error') or 'API вернул success=false'}")
        return payload
    raise RuntimeError(f"Euro365: запрос {path} не выполнен: {last_error}")


def fixture_record(event_id, event):
    """One tomorrow fixture: identifiers only, never odds."""
    kickoff = event_kickoff(event)
    home, away = event_home_away(event)
    return {
        "eventId": event_id,
        "time": kickoff.isoformat(),
        "league": event_league(event),
        "home": home,
        "away": away,
    }


def fetch_tomorrow_fixtures(api_key, target_date):
    """All Euro365 prematch football events of the target Moscow day."""
    payload = request_json("/v1/prematch?sport=1", api_key)
    events = dict(event_entries(payload))
    fixtures = []
    for event_id, event in events.items():
        kickoff = event_kickoff(event)
        if kickoff is None or moscow_day_key(kickoff) != target_date:
            continue
        fixtures.append(fixture_record(event_id, event))
    fixtures.sort(key=lambda f: (f["time"], f["league"], f["home"]))
    return fixtures, {"prematch": len(events)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/fixtures-tomorrow.json",
                        help="where to write the tomorrow fixture list")
    parser.add_argument("--date", default=None,
                        help="target Moscow day YYYY-MM-DD (default: tomorrow)")
    args = parser.parse_args(argv)

    api_key = os.environ.get("EURO365_API_KEY", "").strip()
    if not api_key:
        print("EURO365_API_KEY не задан — список завтрашних матчей не обновлён, страница покажет пустое состояние")
        return 0

    if args.date:
        target_date = args.date
    else:
        target_date = moscow_day_key(datetime.now(timezone.utc) + timedelta(days=1))

    fixtures, diagnostics = fetch_tomorrow_fixtures(api_key, target_date)
    artifact = {
        "date": target_date,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "fixtures": fixtures,
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print("Euro365 завтра ({date}): prematch={prematch}, отобрано={rows}".format(
        date=target_date, rows=len(fixtures), **diagnostics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
