"""Static guards for the scheduled lines-to-homepage publication workflow."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / ".github/workflows/acquire-bookmaker-lines.yml").read_text(encoding="utf-8")
DEPLOY = (ROOT / ".github/workflows/deploy-pages.yml").read_text(encoding="utf-8")


class DailyAutomationWorkflowTest(unittest.TestCase):
    def test_schedule_manual_dispatch_and_non_cancelling_concurrency(self):
        self.assertIn("workflow_dispatch:", PIPELINE)
        self.assertIn('cron: "35 6 * * *"', PIPELINE)
        self.assertIn("group: daily-coupon-pipeline", PIPELINE)
        self.assertIn("cancel-in-progress: false", PIPELINE)
        self.assertIn('ref: main', PIPELINE)

    def test_stale_snapshot_is_rejected_before_any_bookmaker_request(self):
        guard = PIPELINE.index("- name: Validate current model snapshot")
        acquisition = PIPELINE.index("- name: Acquire fresh lines and matched detail markets")
        self.assertLess(guard, acquisition)
        self.assertIn("data.date !== targetDate", PIPELINE)
        self.assertIn("no coupon artifact will be published", PIPELINE)
        self.assertIn("Array.isArray(data.predictions)", PIPELINE)

    def test_acquisition_and_coupon_contracts_run_before_publication(self):
        tests = PIPELINE.index("- name: Test acquisition, pricing and homepage contracts")
        acquisition = PIPELINE.index("- name: Acquire fresh lines and matched detail markets")
        commit = PIPELINE.index("- name: Commit only the dated homepage artifact")
        self.assertLess(tests, acquisition)
        self.assertLess(acquisition, commit)
        self.assertIn("tests.test_odds_acquisition", PIPELINE)
        self.assertIn("tests/daily-coupons.test.cjs", PIPELINE)
        self.assertIn("tests/daily-board.test.cjs", PIPELINE)
        self.assertIn("board.normalizeCoupons(artifact)", PIPELINE)
        self.assertIn("source_unavailable", PIPELINE)
        self.assertIn("no_line_match", PIPELINE)
        self.assertIn("captured_at_utc", PIPELINE)
        self.assertIn("coupon.status !== 'prepared'", PIPELINE)
        self.assertIn("board.gameSessionForTime(coupon.legs?.[0]?.time)", PIPELINE)
        self.assertIn("coupon.session_id !== session.id", PIPELINE)
        self.assertIn("sessionCounts[session.id][coupon.type]", PIPELINE)
        self.assertNotIn("coupons.filter(item => item.type === 'single').length > 2", PIPELINE)

    def test_only_the_dated_homepage_json_can_be_committed(self):
        self.assertIn('ARTIFACT="analysis/doubles_${TARGET_DATE}.json"', PIPELINE)
        self.assertIn('git add -- "$ARTIFACT"', PIPELINE)
        self.assertIn('test "$(git diff --cached --name-only)" = "$ARTIFACT"', PIPELINE)
        self.assertNotIn("git add -A", PIPELINE)
        self.assertNotIn("git add probe-out", PIPELINE)
        self.assertNotIn("git add *.csv", PIPELINE)
        self.assertIn("if-no-files-found: warn", PIPELINE)
        self.assertIn("probe-out/lines_${{ steps.date.outputs.date }}/", PIPELINE)

    def test_pages_deploys_daily_artifacts_after_bot_commits(self):
        self.assertIn('"analysis/*.json"', DEPLOY)
        self.assertIn('"Формирование купонов"', DEPLOY)
        self.assertIn("types: [completed]", DEPLOY)
        self.assertIn("workflow_run.conclusion == 'success'", DEPLOY)


if __name__ == "__main__":
    unittest.main()
