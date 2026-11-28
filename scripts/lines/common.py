"""Shared HTTP, time and persistence helpers for bookmaker line acquisition."""

from __future__ import annotations

import gzip
import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any, Callable

MOSCOW_TZ = timezone(timedelta(hours=3))
MAX_ATTEMPTS = 3


class AcquisitionError(RuntimeError):
    """A source error with a stable machine-readable status."""

    def __init__(self, status: str, message: str, *, url: str = "", http_status: int | None = None):
        super().__init__(message)
        self.status = status
        self.url = url
        self.http_status = http_status

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": self.status, "message": str(self)}
        if self.url:
            result["url"] = self.url
        if self.http_status is not None:
            result["http_status"] = self.http_status
        return result


class MarketAbsentError(ValueError):
    """The event was received, but no supported detail market was present."""


@dataclass
class FetchResult:
    payload: Any
    raw_body: bytes
    url: str
    requested_at_utc: str
    received_at_utc: str
    http_status: int
    attempts: int

    def metadata(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "requested_at_utc": self.requested_at_utc,
            "received_at_utc": self.received_at_utc,
            "http_status": self.http_status,
            "attempts": self.attempts,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_moment(value: Any) -> datetime:
    """Parse an ISO timestamp or Unix seconds/milliseconds into aware UTC."""
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        numeric = float(value)
        if numeric > 100_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None and math.isfinite(numeric):
        if numeric > 100_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no timezone: {text}")
    return parsed.astimezone(timezone.utc)


def moscow_day(value: Any) -> str:
    return parse_moment(value).astimezone(MOSCOW_TZ).date().isoformat()


def moscow_game_day(value: Any) -> str:
    """Return the date on which the 12:00–next-07:00 Moscow game day starts."""
    local = parse_moment(value).astimezone(MOSCOW_TZ)
    if local.hour * 60 + local.minute < 420:
        local -= timedelta(days=1)
    return local.date().isoformat()


def valid_odds(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 1


def normalize_team(value: Any) -> str:
    """Canonical comparison key; no fuzzy matching or identity guessing."""
    text = str(value or "").replace("Ø", "O").replace("ø", "o")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("ё", "е").replace("&", " and ")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    aliases = {
        "turkiye": "turkey",
        "czechia": "czech republic",
        "republic of ireland": "ireland",
        "bosnia herzegovina": "bosnia and herzegovina",
        # Exact club-name variants observed in bookmaker snapshots. These are
        # explicit identities, not fuzzy or partial team-name matching.
        "cr vasco da gama": "vasco da gama",
        "vasco da gama rj": "vasco da gama",
        "sport club internacional": "internacional",
        "internacional rs": "internacional",
        "ec vitoria salvador": "vitoria salvador",
        "vitoria ba": "vitoria salvador",
        "red bull bragantino": "bragantino",
        "rb bragantino": "bragantino",
        "fluminense rj": "fluminense",
        "orlando city fc": "orlando city",
        "fc cincinnati": "cincinnati",
        "1 magdeburg": "magdeburg",
        "1 heidenheim": "heidenheim",
        "en avant de guingamp": "guingamp",
        "clermont foot": "clermont",
        "rodez aveyron": "rodez",
        "dijon fco": "dijon",
        "red star saint ouen": "red star",
        "bayern": "bayern munich",
        "1 koln": "koln",
        "hellas verona": "verona",
        "andorra andorra la vella": "andorra",
        "fc andorra": "andorra",
        "troyes ac": "troyes",
        "rodina": "rodina moscow",
        # Club prefixes/suffixes and bilingual display variants observed in
        # bookmaker snapshots (audit 2026-11-25: 303 no_line_match records).
        "sk slavia praha": "slavia prague",
        "slavia praha": "slavia prague",
        "sabah fk": "sabah",
        "sabah baku": "sabah",
        "fc barcelona": "barcelona",
        "psg": "paris saint germain",
        "as roma": "roma",
        "us avellino 1912": "avellino",
        "avellino 1912": "avellino",
        "calcio padova": "padova",
        "a s d atletico arezzo": "arezzo",
        "viking fk": "viking",
        "club brugge kv": "brugge",
        "club brugge": "brugge",
        "liverpool fc": "liverpool",
        "inter milan": "inter",
        "internazionale milano": "inter",
        "vfb stuttgart": "stuttgart",
        "losc lille": "lille",
        "lille osc": "lille",
        "fc bayern munchen": "bayern munich",
        "sporting cp": "sporting lisbon",
        "sporting clube de portugal": "sporting lisbon",
        "corinthians sp": "corinthians",
        "sporting lisboa": "sporting lisbon",
        "atletico mg": "atletico mineiro",
        "west bromwich albion": "west bromwich",
        "birmingham city": "birmingham",
        # Russian display names used by Winline (unlinked events) and Marathonbet.
        "сабах": "sabah",
        "барселона": "barcelona",
        "славия прага": "slavia prague",
        "вильярреал": "villarreal",
        "кремонезе": "cremonese",
        "авеллино": "avellino",
        "падова": "padova",
        "сампдория": "sampdoria",
        "мантова": "mantova",
        "ареццо": "arezzo",
        "пиза": "pisa",
        "модена": "modena",
        "уотфорд": "watford",
        "блэкберн роверс": "blackburn rovers",
        "бернли": "burnley",
        "бирмингем": "birmingham",
        "болтон уондерерс": "bolton wanderers",
        "вест бромвич": "west bromwich",
        "сток сити": "stoke city",
        "миллуолл": "millwall",
        "атлетико м": "atletico madrid",
        "атлетико мадрид": "atletico madrid",
        "викинг": "viking",
        "брюгге": "brugge",
        "ливерпуль": "liverpool",
        "интер м": "inter",
        "интер": "inter",
        "штутгарт": "stuttgart",
        "лилль": "lille",
        "бавария": "bayern munich",
        "псж": "paris saint germain",
        "рома": "roma",
        "спортинг л": "sporting lisbon",
        "спортинг лиссабон": "sporting lisbon",
        "манчестер юнайтед": "manchester united",
        "манчестер юнаитед": "manchester united",
        "шахтер д": "shakhtar donetsk",
        "фенербахче": "fenerbahce",
        "атлетико минейро": "atletico mineiro",
        "атлетико минеиро": "atletico mineiro",
        "коринтианс": "corinthians",
        "коринтианс сп": "corinthians",
        # ЛЕ/ЛК-матчдэй 26.11.2026 (аудит: 109 no_line_match из 129 записей):
        # префиксы/суффиксы латиницей и кириллические display-имена БК.
        "fc kairat almaty": "kairat almaty",
        "каират": "kairat almaty",
        "каират алматы": "kairat almaty",
        "mjallby aif": "mjallby",
        "mjolby": "mjallby",
        "мьельбю": "mjallby",
        "afc ajax": "ajax",
        "аякс": "ajax",
        "fc thun": "thun",
        "тун": "thun",
        "besiktas jk": "besiktas",
        "бешикташ": "besiktas",
        "hapoel be er sheva": "hapoel beer sheva",
        "хапоэль беэр шева": "hapoel beer sheva",
        "хапоэль беер шева": "hapoel beer sheva",
        "brighton and hove albion": "brighton",
        "brighton hove albion": "brighton",
        "браитон": "brighton",
        "университатя краиова": "universitatea craiova",
        "celta vigo": "celta",
        "celta de vigo": "celta",
        "сельта": "celta",
        "борнмут": "bournemouth",
        "fc iberia 1999": "iberia 1999",
        "иберия 1999": "iberia 1999",
        "хетафе": "getafe",
        "fc viktoria plzen": "viktoria plzen",
        "виктория пльзень": "viktoria plzen",
        "виктория плзень": "viktoria plzen",
        "бенфика": "benfica",
        "fk jablonec": "jablonec",
        "яблонец": "jablonec",
        "fc lugano": "lugano",
        "лугано": "lugano",
        "fk kauno zalgiris": "kauno zalgiris",
        "кауно жальгирис": "kauno zalgiris",
        "жальгирис каунас": "kauno zalgiris",
        "fc riga": "riga",
        "riga fc": "riga",
        "рига фк": "riga",
        "gnk dinamo zagreb": "dinamo zagreb",
        "динамо загреб": "dinamo zagreb",
        "bayer 04 leverkusen": "bayer leverkusen",
        "баиер 04": "bayer leverkusen",
        "баиер л": "bayer leverkusen",
        "heart of midlothian": "hearts",
        "хартс": "hearts",
        "as monaco": "monaco",
        "монако": "monaco",
        "kf egnatia": "egnatia",
        "egnatia rrogozhine": "egnatia",
        "эгнатия": "egnatia",
        "эгнатия рогожина": "egnatia",
        "линкольн ред импс": "lincoln red imps",
        "линкольн": "lincoln red imps",
        "olympiacos fc": "olympiacos",
        "olympiacos piraeus": "olympiacos",
        "олимпиакос": "olympiacos",
        "олимпиакос пиреи": "olympiacos",
        "ac milan": "milan",
        "милан": "milan",
        "olympique de marseille": "marseille",
        "марсель": "marseille",
        "левски софия": "levski sofia",
        "pafos fc": "pafos",
        "пафос": "pafos",
        "fc midtjylland": "midtjylland",
        "мидтъюлланн": "midtjylland",
        "мидтьюлланн": "midtjylland",
        "red bull salzburg": "salzburg",
        "ред булл зальцбург": "salzburg",
        "рб зальцбург": "salzburg",
        "fc ararat armenia": "ararat armenia",
        "арарат армения": "ararat armenia",
        "royale union saint gilloise": "union saint gilloise",
        "юнион сен жилуаз": "union saint gilloise",
        "юнион сент жиллуаз": "union saint gilloise",
        "лех познань": "lech poznan",
        "лех": "lech poznan",
        "sk brann": "brann",
        "бранн": "brann",
        "agf aarhus": "agf",
        "орхус": "agf",
        "ac sparta praha": "sparta prague",
        "sparta praha": "sparta prague",
        "спарта прага": "sparta prague",
        "аз алкмаар": "az alkmaar",
        "аз алкмар": "az alkmaar",
        "sporting braga": "braga",
        "спортинг брага": "braga",
        "брага": "braga",
        "kuopion palloseura": "kups",
        "kups kuopio": "kups",
        "купс": "kups",
        "fk borac banja luka": "borac banja luka",
        "борац баня лука": "borac banja luka",
        "аталанта": "atalanta",
        "ferencvaros tc": "ferencvaros",
        "ferencvarosi": "ferencvaros",
        "ференцварош": "ferencvaros",
        "nk celje": "celje",
        "целе": "celje",
        "hnk hajduk split": "hajduk split",
        "хаидук сплит": "hajduk split",
        "sint truidense vv": "sint truiden",
        "sint truidense": "sint truiden",
        "st truiden": "sint truiden",
        "сент трюиден": "sint truiden",
        "inter club d escaldes": "inter escaldes",
        "интер эскальдес": "inter escaldes",
        "фк копенгаген": "copenhagen",
        "копенгаген": "copenhagen",
        "kaa gent": "gent",
        "гент": "gent",
        "fk crvena zvezda": "crvena zvezda",
        "црвена звезда": "crvena zvezda",
        "нек неимеген": "nec nijmegen",
        "stade rennais": "rennes",
        "rennais": "rennes",
        "ренн": "rennes",
        "ofi crete": "ofi",
        "офи": "ofi",
        "офи крит": "ofi",
        "rsc anderlecht": "anderlecht",
        "андерлехт": "anderlecht",
        "sc freiburg": "freiburg",
        "фраибург": "freiburg",
        "fc twente": "twente",
        "твенте": "twente",
        "tsg 1899 hoffenheim": "hoffenheim",
        "tsg hoffenheim": "hoffenheim",
        "1899 hoffenheim": "hoffenheim",
        "хоффенхаим": "hoffenheim",
        "sk sturm graz": "sturm graz",
        "sturm": "sturm graz",
        "штурм грац": "sturm graz",
        "sao paulo fc": "sao paulo",
        "сан паулу": "sao paulo",
        "сан паулу сп": "sao paulo",
        "clube do remo": "remo",
        "remo belem": "remo",
        "ремо": "remo",
        "ремо па": "remo",
        # Slug-формы Марафонбет (match_lines сопоставляет их вместо display-имён)
        # и остатки аудита 26.11.2026.
        "kairat": "kairat almaty",
        "paphos": "pafos",
        "olympiacos pireas": "olympiacos",
        "fc kobenhavn": "copenhagen",
        "рига": "riga",
        "торренсе": "torreense",
        "селтик": "celtic",
        "huddersfield": "huddersfield town",
        # Чемпионатский уикенд 27–28.11.2026 (аудит: 176 no_line_match из 339
        # записей; сюда вошли и пары дня 27.11, не закоммиченные отдельно).
        "hertha bsc": "hertha berlin",
        "holstein": "holstein kiel",
        "1 kaiserslautern": "kaiserslautern",
        "1 fc heidenheim": "heidenheim",
        "1 fc nurnberg": "nurnberg",
        "vfl bochum": "bochum",
        "vfl bochum 1848": "bochum",
        "1 fc koln": "koln",
        "sv 07 elversberg": "elversberg",
        "1 fsv mainz 05": "mainz",
        "mainz 05": "mainz",
        "1 fc union berlin": "union berlin",
        "hamburger sv": "hamburger",
        "sc paderborn 07": "paderborn",
        "paderborn 07": "paderborn",
        "fc schalke 04": "schalke 04",
        "vfl osnabruck": "osnabruck",
        "sv werder bremen": "werder bremen",
        "borussia m gladbach": "borussia monchengladbach",
        "аинтрахт ф": "eintracht frankfurt",
        "wolverhampton wanderers": "wolverhampton",
        "leeds united": "leeds",
        "leicester city": "leicester",
        "queens park rangers": "qpr",
        "rotherham united": "rotherham",
        "oldham athletic": "oldham",
        "west ham united": "west ham",
        "вест хэм": "west ham",
        "wimbledon": "afc wimbledon",
        "бристоль сити": "bristol city",
        "nottingham forest fc": "nottingham forest",
        "chelsea fc": "chelsea",
        "southampton fc": "southampton",
        "middlesbrough fc": "middlesbrough",
        "zenit saint petersburg": "zenit spb",
        "zenit st petersburg": "zenit spb",
        "baltika kaliningrad": "baltika",
        "akhmat grozny": "akhmat",
        "ascoli calcio 1898": "ascoli",
        "carrarese calcio": "carrarese",
        "frosinone calcio": "frosinone",
        "l r vicenza": "vicenza",
        "vicenza calcio": "vicenza",
        "citta di palermo": "palermo",
        "benevento calcio": "benevento",
        "unione venezia": "venezia",
        "bologna 1909": "bologna",
        "bologna fc 1909": "bologna",
        "real betis": "betis",
        "albacete balompie": "albacete",
        "cd tenerife": "tenerife",
        "cd eldense": "eldense",
        "eldence": "eldense",
        "girona fc": "girona",
        "malaga cf": "malaga",
        "athletic club": "athletic bilbao",
        "levante ud": "levante",
        "racing de santander": "racing santander",
        "real racing club de santander": "racing santander",
        "deportivo alaves": "alaves",
        "real valladolid": "valladolid",
        "real sociedad ii": "real sociedad 2",
        "real sociedad b": "real sociedad 2",
        "burgos": "burgos cf",
        "burgos club de futbol": "burgos cf",
        "stade brestois 29": "brest",
        "stade brestois": "brest",
        "paris": "paris fc",
        "angers sco": "angers",
        "rc lens": "lens",
        "rc strasbourg": "strasbourg",
        "ogc nice": "nice",
        "fc groningen": "groningen",
        "willem ii tilburg": "willem ii",
        "sc telstar": "telstar",
        "psv eindhoven": "psv",
        "sc cambuur": "cambuur",
        "fc arouca": "arouca",
        "academico de viseu": "academico viseu",
        "academico viseu fc": "academico viseu",
        "fc alverca": "alverca",
        "vitoria sc": "vitoria guimaraes",
        "estrela da amadora": "estrela amadora",
        "cf estrela amadora": "estrela amadora",
        "cs maritimo": "maritimo",
        "cd nacional": "nacional madeira",
        "nacional da madeira": "nacional madeira",
        "dundee fc": "dundee",
        "falkirk fc": "falkirk",
        "st mirren": "saint mirren",
        "iraklis thessaloniki": "iraklis 1908 thessaloniki",
        "volos nfc": "volos",
        "kifisias": "kifisia",
        "gaziantep": "gaziantep bb",
        "gaziantep fk": "gaziantep bb",
        "gazisehir gaziantep": "gaziantep bb",
        "caykur rizespor": "rizespor",
        "kasimpasa sk": "kasimpasa",
        "rosenborg bk": "rosenborg",
        "molde fk": "molde",
        "charlotte fc": "charlotte",
        "nashville sc": "nashville",
        "fluminense fc": "fluminense",
        "cruzeiro ec": "cruzeiro",
        "cruzeiro mg": "cruzeiro",
        # Русскоязычные написания Winline для событий, которые не удалось
        # связать со слагами Marathonbet (тонкая линия Marathonbet): мост
        # link_winline_identities их не покрывает, поэтому идентичность
        # фиксируется здесь явно. Аудит 28.11.2026: 67 no_line_match.
        "эксетер сити": "exeter city",
        "кроули таун": "crawley town",
        "мк донс": "milton keynes dons",
        "ноттс каунти": "notts county",
        "престон": "preston north end",
        "норвич сити": "norwich city",
        "линкольн сити": "lincoln city",
        "вулверхэмптон": "wolverhampton",
        "цска м": "cska moscow",
        "зенит спб": "zenit spb",
        "каррарезе": "carrarese",
        "юве стабиа": "juve stabia",
        "падерборн": "paderborn",
        "шальке": "schalke 04",
        "маинц": "mainz",
        "унион берлин": "union berlin",
        "кельн": "koln",
        "эльферсберг": "elversberg",
        "ротерхэм юнаитед": "rotherham",
        "олдхэм": "oldham",
        "порт вэил": "port vale",
        "ньюпорт каунти": "newport county",
        "флитвуд таун": "fleetwood town",
        "аккрингтон стенли": "accrington stanley",
        "уиком уондерерс": "wycombe wanderers",
        "бредфорд сити": "bradford city",
        "стевенэидж": "stevenage",
        "блэкпул": "blackpool",
        "шеффилд уэнсдеи": "sheffield wednesday",
        "рединг": "reading",
        "лестер сити": "leicester",
        "мансфилд таун": "mansfield town",
        "уимблдон": "afc wimbledon",
        "барнсли": "barnsley",
        "рексхем": "wrexham",
        "портсмут": "portsmouth",
        "кпр": "qpr",
        "шеффилд юнаитед": "sheffield united",
        "глазго реинджерс": "rangers",
        "фалкирк": "falkirk",
        "данди юнаитед": "dundee united",
        "ст джонстон": "st johnstone",
        "абердин": "aberdeen",
        "данди": "dundee",
        "волос": "volos",
        "астерас триполис": "asteras tripolis",
        "по триглия ираклис": "iraklis 1908 thessaloniki",
        "панетоликос": "panetolikos",
        "мореиренсе": "moreirense",
        "санта клара": "santa clara",
        "арока": "arouca",
        "риу аве": "rio ave",
        "эксельсиор роттердам": "excelsior",
        "телстар": "telstar",
        "газиантеп": "gaziantep bb",
        "амедспор": "amedspor",
        "балтика": "baltika",
        "ахмат": "akhmat",
        "русенборг": "rosenborg",
        "мольде": "molde",
        "шарлотт": "charlotte",
        "чикаго": "chicago fire",
        "псв эиндховен": "psv",
        "гоу эхед иглс": "go ahead eagles",
        "академика визеу": "academico viseu",
        "алверка": "alverca",
        "кифисия": "kifisia",
        "каламата": "kalamata",
        "хьюстон": "houston dynamo",
        "сан хосе": "san jose earthquakes",
        "оснабрюк": "osnabruck",
        "ганновер": "hannover 96",
        "реал м": "real madrid",
        "алавес": "alaves",
        "вальядолид": "valladolid",
        "реал сосьедад б": "real sociedad 2",
        "витория гимараинш": "vitoria guimaraes",
        "эштрела": "estrela amadora",
        "филадельфия": "philadelphia union",
        "нью ингланд": "new england revolution",
        "цинциннати": "cincinnati",
        "нэшвилл": "nashville",
    }
    return aliases.get(text, text)


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 60.0)
        except ValueError:
            pass
    return min(0.5 * (2 ** (attempt - 1)), 4.0)


def _decode_body(body: bytes) -> bytes:
    return gzip.decompress(body) if body.startswith(b"\x1f\x8b") else body


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    attempts: int = MAX_ATTEMPTS,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> FetchResult:
    """GET JSON with bounded retries; never follows an anti-bot challenge."""
    last_error: AcquisitionError | None = None
    for attempt in range(1, attempts + 1):
        requested_at = utc_now_iso()
        request = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
        try:
            with opener(request, timeout=timeout) as response:
                body = _decode_body(response.read())
                status = int(getattr(response, "status", 200))
                final_url = str(getattr(response, "url", "") or url)
            stripped = body.lstrip().lower()
            if stripped.startswith((b"<!doctype html", b"<html")):
                text = body[:4096].decode("utf-8", errors="replace").casefold()
                status_name = "captcha" if "captcha" in text else "access_blocked"
                raise AcquisitionError(status_name, "Источник вернул HTML вместо JSON; обход не выполняется", url=url, http_status=status)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AcquisitionError("parse_error", f"Ответ не является валидным JSON: {exc}", url=url, http_status=status) from exc
            if not isinstance(payload, (dict, list)):
                raise AcquisitionError("schema_changed", "JSON верхнего уровня должен быть объектом или массивом", url=url, http_status=status)
            return FetchResult(payload, body, final_url, requested_at, utc_now_iso(), status, attempt)
        except urllib.error.HTTPError as exc:
            # Consume a small error body to close the connection cleanly; never parse it as line data.
            exc.read(4096)
            retryable = exc.code == 429 or exc.code >= 500
            status_name = "access_blocked" if exc.code in (401, 403) else "source_unavailable"
            last_error = AcquisitionError(status_name, f"HTTP {exc.code} для {url}", url=url, http_status=exc.code)
            if retryable and attempt < attempts:
                sleeper(_retry_delay(exc.headers.get("Retry-After") if exc.headers else None, attempt))
                continue
            raise last_error from exc
        except AcquisitionError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, IncompleteRead, OSError) as exc:
            last_error = AcquisitionError("source_unavailable", f"Сбой запроса {url}: {exc}", url=url)
            if attempt < attempts:
                sleeper(_retry_delay(None, attempt))
                continue
            raise last_error from exc
    raise last_error or AcquisitionError("source_unavailable", f"Запрос не выполнен: {url}", url=url)


def write_json(path: Path, payload: Any) -> None:
    """Atomically write deterministic UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_raw(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)
