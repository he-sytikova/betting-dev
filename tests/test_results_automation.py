"""Static guards for public results and browser-local financial reporting."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
REFRESH = (ROOT / ".github/workflows/refresh-dev-fixtures.yml").read_text(encoding="utf-8")
DEPLOY = (ROOT / ".github/workflows/deploy-pages.yml").read_text(encoding="utf-8")
VALIDATE = (ROOT / ".github/workflows/validate-corners-cards-models.yml").read_text(encoding="utf-8")
HOMEPAGE = (ROOT / "index.html").read_text(encoding="utf-8")


class ResultsAutomationTest(unittest.TestCase):
    def test_refresh_publishes_only_normalized_result_json(self):
        self.assertIn("fetch-euro365-results.py", REFRESH)
        self.assertIn("--output-dir data/results", REFRESH)
        self.assertIn("git add data/results/*.json", REFRESH)
        self.assertNotIn("git add probe-out", REFRESH)
        self.assertNotIn("git add *.csv", REFRESH)

    def test_pages_and_validation_include_results_reporting(self):
        self.assertIn('"data/results/*.json"', DEPLOY)
        self.assertIn('"*.css"', DEPLOY)
        self.assertIn("tests/financial-report.test.cjs", VALIDATE)
        self.assertIn("tests.test_euro365_results", VALIDATE)

    def test_homepage_loads_confirmation_before_reporting(self):
        self.assertIn('id="financial-report"', HOMEPAGE)
        confirmation = HOMEPAGE.index('src="coupon-confirmation.js"')
        reporting = HOMEPAGE.index('src="financial-report.js"')
        self.assertLess(confirmation, reporting)
        self.assertIn("FinancialReport.mount", HOMEPAGE)


if __name__ == "__main__":
    unittest.main()
