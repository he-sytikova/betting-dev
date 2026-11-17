#!/usr/bin/env python3
"""Acquire Fonbet/Marathonbet lines for a Moscow day without forming coupons."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lines.acquire import acquire_lines
from scripts.lines.common import MOSCOW_TZ


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(MOSCOW_TZ).date().isoformat(),
                        help="Moscow date YYYY-MM-DD (default: today)")
    parser.add_argument("--fixtures", type=Path, default=Path("data.json"),
                        help="data.json, fixtures-<date>.json or candidate fixture JSON")
    parser.add_argument("--output", type=Path, default=None,
                        help="artifact directory (default: probe-out/lines_<date>)")
    parser.add_argument("--detail-limit", type=int, default=40,
                        help="maximum matched BK-event detail requests, 0..40")
    args = parser.parse_args(argv)
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date().isoformat()
    output = args.output or Path("probe-out") / f"lines_{target_date}"
    summary = acquire_lines(target_date, args.fixtures, output, limit=args.detail_limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
