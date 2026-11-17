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
