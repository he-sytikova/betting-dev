"""Read-only Winline DOM acquisition and conservative pure parsers.

Only the public UEFA Nations League tournament is discovered. The browser never
clicks a price, logs in, solves a challenge or interacts with a bet slip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any

from .common import (
    MOSCOW_TZ,
    AcquisitionError,
    MarketAbsentError,
    moscow_day,
    normalize_team,
    utc_now_iso,
    valid_odds,
    write_json,
)

WINLINE_TOURNAMENT_URL = "https://winline.ru/stavki/sport/futbol/mezhdunarodnye/liga_naczij_uefa"
WINLINE_LEAGUE = "UEFA Nations League"

# Explicit, auditable identities only. Unknown Russian names deliberately stay
# unmatched instead of being transliterated or fuzzily assigned to a fixture.
WINLINE_TEAM_ALIASES = {
    "австрия": "austria",
    "босния и герцеговина": "bosnia and herzegovina",
    "венгрия": "hungary",
    "грузия": "georgia",
    "дания": "denmark",
    "израиль": "israel",
    "ирландия": "ireland",
    "косово": "kosovo",
    "норвегия": "norway",
    "польша": "poland",
    "португалия": "portugal",
    "румыния": "romania",
    "северная ирландия": "northern ireland",
    "украина": "ukraine",
    "уэльс": "wales",
    "швеция": "sweden",
}

_TOURNAMENT_EXTRACTOR = r"""
() => [...document.querySelectorAll('a[href^="/stavki/event/"]')].map(link => {
  const matchId = (link.getAttribute('href').match(/^\/stavki\/event\/(\d+)$/) || [])[1];
  const names = [...link.querySelectorAll('.body-left__names > .name')]
    .map(node => node.textContent.trim()).filter(Boolean);
  const card = link.closest('.card__competitors');
  const startText = card?.querySelector('.header-left__time')?.textContent.trim() || '';
  return {match_id: matchId || '', source_teams: names, start_text: startText};
}).filter(row => row.match_id)
"""

_DETAIL_EXTRACTOR = r"""
() => [...document.querySelectorAll('.market')].map(section => ({
  title: section.querySelector(':scope > button.market-header h5')?.textContent.trim() || '',
  rows: [...section.querySelectorAll(':scope > .market__content .market__row')].map(row => ({
    label: row.querySelector(':scope > .market__row-text')?.textContent.trim() || '',
    selections: [...row.querySelectorAll(':scope > button.market__row-btn')].map(button => ({
      label: button.querySelector('.market-row-btn__text')?.textContent.trim() || '',
      odds_text: button.querySelector('.market-row-btn__coef')?.textContent.trim() || '',
      disabled: button.disabled,
    })),
  })),
})).filter(section => section.title)
"""


@dataclass
class WinlineResult:
    payload: Any
    url: str
    requested_at_utc: str
    received_at_utc: str

    def metadata(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "requested_at_utc": self.requested_at_utc,
            "received_at_utc": self.received_at_utc,
            "transport": "playwright_dom",
            "attempts": 1,
        }


def canonical_winline_team(value: Any) -> str:
    normalized = normalize_team(value)
    return WINLINE_TEAM_ALIASES.get(normalized, normalized)


def parse_winline_start(value: Any, now: datetime) -> datetime:
    """Parse the visible Moscow-local start label without guessing a timezone."""
    text = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    local_now = now.astimezone(MOSCOW_TZ)
    relative = re.fullmatch(r"(сегодня|завтра) ([01]\d|2[0-3]):([0-5]\d)", text)
    if relative:
        day = local_now.date() + (timedelta(days=1) if relative.group(1) == "завтра" else timedelta())
        hour, minute = int(relative.group(2)), int(relative.group(3))
        return datetime.combine(day, datetime_time(hour, minute), tzinfo=MOSCOW_TZ).astimezone(timezone.utc)
    explicit = re.fullmatch(
        r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))? ([01]\d|2[0-3]):([0-5]\d)", text
    )
    if not explicit:
        raise ValueError(f"unsupported Winline start label: {value!r}")
    day, month = int(explicit.group(1)), int(explicit.group(2))
    year = int(explicit.group(3)) if explicit.group(3) else local_now.year
    if year < 100:
        year += 2000
    hour, minute = int(explicit.group(4)), int(explicit.group(5))
    candidate = datetime(year, month, day, hour, minute, tzinfo=MOSCOW_TZ)
    if not explicit.group(3):
        if candidate - local_now > timedelta(days=183):
            candidate = candidate.replace(year=year - 1)
        elif local_now - candidate > timedelta(days=183):
            candidate = candidate.replace(year=year + 1)
    return candidate.astimezone(timezone.utc)


def parse_winline_snapshot(
    payload: Any,
    target_date: str,
    *,
    tournament_url: str = WINLINE_TOURNAMENT_URL,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("Winline tournament payload must be a list")
    current = now or datetime.now(timezone.utc)
    by_id: dict[str, dict[str, Any]] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        match_id = str(row.get("match_id") or "")
        teams = row.get("source_teams")
        if not re.fullmatch(r"\d+", match_id) or not isinstance(teams, list) or len(teams) != 2:
            continue
        source_home, source_away = (str(team).strip() for team in teams)
        if not source_home or not source_away:
            continue
        try:
            start = parse_winline_start(row.get("start_text"), current)
        except (ValueError, TypeError, OverflowError):
            continue
        if moscow_day(start) != target_date:
            continue
        line = {
            "bookmaker": "Winline",
            "match_id": match_id,
            "tree_id": None,
            "team1": canonical_winline_team(source_home),
            "team2": canonical_winline_team(source_away),
            "source_team1": source_home,
            "source_team2": source_away,
            "league": WINLINE_LEAGUE,
            "start_time": start.isoformat(),
            "detail_url": tournament_url.rstrip("/") + "/" + match_id,
            "odds": {},
            "markets": {},
        }
        previous = by_id.get(match_id)
        if previous is not None and previous != line:
            raise ValueError(f"conflicting duplicate Winline event {match_id}")
        by_id[match_id] = line
    return sorted(by_id.values(), key=lambda item: (item["start_time"], item["team1"], item["match_id"]))


def _decimal_odds(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if valid_odds(result) else None


def _put_market(markets: dict[str, dict[str, float]], key: str, side: str, odds: float) -> None:
    values = markets.setdefault(key, {})
    previous = values.get(side)
    if previous is not None and abs(previous - odds) > 1e-12:
        raise ValueError(f"conflicting Winline prices for {key}:{side}")
    values[side] = odds


def _total_market(title: str, home: str, away: str) -> str | None:
    if title == "Тотал (осн.время)":
        return "TOTAL_GOALS"
    if title == f"Тотал (осн.время) {home}":
        return "IT_HOME_GOALS"
    if title == f"Тотал (осн.время) {away}":
        return "IT_AWAY_GOALS"
    if title == "Тотал угловых (осн.время)":
        return "TOTAL_CORNERS"
    if title == f"Тотал угловых (осн.время) {home}":
        return "IT_HOME_CORNERS"
    if title == f"Тотал угловых (осн.время) {away}":
        return "IT_AWAY_CORNERS"
    return None


def parse_winline_detail(payload: Any, home: str, away: str) -> dict[str, Any]:
    """Extract only explicitly supported full-time markets from DOM rows."""
    if not isinstance(payload, dict):
        raise ValueError("Winline detail payload must be an object")
    sections = payload.get("markets")
    if not isinstance(sections, list) or not sections:
        raise ValueError("Winline detail contains no market sections")
    odds_1x2: dict[str, float] = {}
    markets: dict[str, dict[str, float]] = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        rows = section.get("rows")
        if not isinstance(rows, list):
            continue
        if title in ("Исход 1X2", "Двойной шанс"):
            row = next((item for item in rows if isinstance(item, dict)
                        and str(item.get("label") or "").strip() == "осн.время"), None)
            if row is None:
                continue
            expected = {"П1": "home", "X": "draw", "П2": "away"} if title == "Исход 1X2" else None
            target = odds_1x2 if expected is not None else markets.setdefault("DC", {})
            for selection in row.get("selections", []):
                if not isinstance(selection, dict) or selection.get("disabled"):
                    continue
                label = str(selection.get("label") or "").strip()
                side = expected.get(label) if expected is not None else label
                if side not in ({"home", "draw", "away"} if expected is not None else {"1X", "12", "X2"}):
                    continue
                price = _decimal_odds(selection.get("odds_text"))
                if price is None:
                    continue
                if expected is None:
                    _put_market(markets, "DC", side, price)
                else:
                    previous = target.get(side)
                    if previous is not None and abs(previous - price) > 1e-12:
                        raise ValueError(f"conflicting Winline 1X2 price for {side}")
                    target[side] = price
            continue
        model = _total_market(title, home, away)
        if model is None:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_label = str(row.get("label") or "").strip()
            if row_label not in ("Больше", "Меньше"):
                continue
            for selection in row.get("selections", []):
                if not isinstance(selection, dict) or selection.get("disabled"):
                    continue
                label = str(selection.get("label") or "").strip().casefold().replace(",", ".")
                match = re.fullmatch(r"[бм]\s*(\d+(?:\.\d+)?)", label)
                if not match:
                    continue
                price = _decimal_odds(selection.get("odds_text"))
                if price is None:
                    continue
                over = label.startswith("б")
                if model.startswith("IT_HOME_"):
                    side = "ИТБ1" if over else "ИТМ1"
                elif model.startswith("IT_AWAY_"):
                    side = "ИТБ2" if over else "ИТМ2"
                else:
                    side = "ТБ" if over else "ТМ"
                _put_market(markets, f"{model}:{match.group(1)}", side, price)
    result = dict(markets)
    if odds_1x2:
        result["1X2"] = odds_1x2
    if not result:
        raise MarketAbsentError("Winline: поддерживаемые рынки основного времени отсутствуют")
    return result


class WinlineBrowser:
    """One bounded Chromium session shared by tournament and detail pages."""

    def __init__(self, *, timeout_ms: int = 45_000):
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow")
            self._page = self._context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
            return self._page
        except Exception as exc:
            self.close()
            raise AcquisitionError("source_unavailable", f"Winline: браузер недоступен ({type(exc).__name__})") from exc

    def _load(self, url: str, selector: str) -> tuple[Any, str, str]:
        page = self._ensure_page()
        requested = utc_now_iso()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_selector(selector, state="attached", timeout=self.timeout_ms)
            body = page.locator("body").inner_text()
            if re.search(r"captcha|капч|доступ ограничен|access denied", body, re.I):
                raise AcquisitionError("access_blocked", "Winline: страница содержит ограничение доступа", url=url)
            return page, requested, page.url
        except AcquisitionError:
            raise
        except Exception as exc:
            raise AcquisitionError(
                "source_unavailable", f"Winline: страница или DOM недоступны ({type(exc).__name__})", url=url
            ) from exc

    def tournament(self) -> WinlineResult:
        page, requested, final_url = self._load(WINLINE_TOURNAMENT_URL, 'a[href^="/stavki/event/"]')
        try:
            payload = page.evaluate(_TOURNAMENT_EXTRACTOR)
        except Exception as exc:
            raise AcquisitionError("schema_changed", f"Winline: не удалось прочитать карточки ({type(exc).__name__})") from exc
        return WinlineResult(payload, final_url, requested, utc_now_iso())

    def detail(self, url: str) -> WinlineResult:
        page, requested, final_url = self._load(url, "button.market-header")
        try:
            payload = {"markets": page.evaluate(_DETAIL_EXTRACTOR)}
        except Exception as exc:
            raise AcquisitionError("schema_changed", f"Winline: не удалось прочитать роспись ({type(exc).__name__})") from exc
        return WinlineResult(payload, final_url, requested, utc_now_iso())

    def close(self) -> None:
        for resource in (self._context, self._browser):
            try:
                if resource is not None:
                    resource.close()
            except Exception:
                pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._page = self._context = self._browser = self._playwright = None


def fetch_winline_snapshot(target_date: str, raw_dir: Path, browser: WinlineBrowser) -> dict[str, Any]:
    try:
        response = browser.tournament()
        raw_path = raw_dir / "winline_tournament.json"
        write_json(raw_path, response.payload)
        matches = parse_winline_snapshot(
            response.payload, target_date, tournament_url=WINLINE_TOURNAMENT_URL
        )
        return {
            "bookmaker": "Winline",
            "target_date": target_date,
            "status": "ok",
            "captured_at_utc": response.received_at_utc,
            "source": response.metadata(),
            "raw_path": raw_path.as_posix(),
            "tournaments": {"NATIONS": {"status": "ok", "url": WINLINE_TOURNAMENT_URL}},
            "matches": matches,
        }
    except AcquisitionError as exc:
        return {
            "bookmaker": "Winline", "target_date": target_date, "status": exc.status,
            "captured_at_utc": utc_now_iso(), "errors": [exc.as_dict()], "matches": [],
        }
    except (ValueError, TypeError, KeyError) as exc:
        error = AcquisitionError("schema_changed", f"Winline: {exc}")
        return {
            "bookmaker": "Winline", "target_date": target_date, "status": error.status,
            "captured_at_utc": utc_now_iso(), "errors": [error.as_dict()], "matches": [],
        }
