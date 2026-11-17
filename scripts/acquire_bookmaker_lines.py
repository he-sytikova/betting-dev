#!/usr/bin/env python3
"""Acquire Fonbet/Marathonbet lines for a Moscow day without forming coupons."""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lines.acquire import acquire_lines
from scripts.lines.common import MOSCOW_TZ, write_json


def apply_model_gate(fixtures_path: Path, output_dir: Path) -> tuple[Path, dict]:
    """Keep only future fixtures that an active monetary model can score."""
    output_dir.mkdir(parents=True, exist_ok=True)
    gated_path = output_dir / "model-fixtures.json"
    script = PROJECT_ROOT / "scripts/lines/filter_model_fixtures.cjs"
    subprocess.run(["node", str(script), str(fixtures_path), str(gated_path)], cwd=PROJECT_ROOT, check=True)
    return gated_path, json.loads(gated_path.read_text(encoding="utf-8"))


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
    gated_fixtures, gate = apply_model_gate(args.fixtures, output)
    summary = acquire_lines(target_date, gated_fixtures, output, limit=args.detail_limit)
    summary["model_gate"] = {
        "source_fixtures": gate["sourceFixtures"],
        "accepted_fixtures": gate["acceptedFixtures"],
        "rejected": gate["rejected"],
    }
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
