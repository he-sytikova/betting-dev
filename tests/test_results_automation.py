"""Static guards for public results and browser-local financial reporting."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
REFRESH = (ROOT / ".github/workflows/refresh-dev-fixtures.yml").read_text(encoding="utf-8")
DEPLOY = (ROOT / ".github/workflows/deploy-pages.yml").read_text(encoding="utf-8")
VALIDATE = (ROOT / ".github/workflows/validate-corners-cards-models.yml").read_text(encoding="utf-8")
HOMEPAGE = (ROOT / "index.html").read_text(encoding="utf-8")
REPORT_PAGE = (ROOT / "financial-report.html").read_text(encoding="utf-8")


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

    def test_reporting_page_loads_confirmation_before_reporting(self):
        self.assertIn('id="financial-report"', REPORT_PAGE)
        confirmation = REPORT_PAGE.index('src="coupon-confirmation.js"')
        reporting = REPORT_PAGE.index('src="financial-report.js"')
        self.assertLess(confirmation, reporting)
        self.assertIn("FinancialReport.mount", REPORT_PAGE)
        self.assertIn('href="./"', REPORT_PAGE)

    def test_homepage_links_to_reporting_without_embedding_operational_blocks(self):
        self.assertIn('href="./financial-report.html"', HOMEPAGE)
        self.assertNotIn('id="financial-report"', HOMEPAGE)
        self.assertNotIn('src="financial-report.js"', HOMEPAGE)
        self.assertNotIn('id="day-odds-block"', HOMEPAGE)
        self.assertNotIn('src="day-odds.js"', HOMEPAGE)
        self.assertNotIn("Кэфы дня", HOMEPAGE)


if __name__ == "__main__":
    unittest.main()
