import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fetch_euro365_results", ROOT / "scripts/models/fetch-euro365-results.py")
results = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(results)


def prediction(**overrides):
    row = {
        "source": "Euro365", "eventId": "e1", "time": "2027-03-10T18:00:00.000Z",
        "league": "League", "home": "Home", "away": "Away", "actualResult": "—",
    }
    row.update(overrides)
    return row


def data(*rows):
    return {"date": "2027-03-10", "predictions": list(rows)}


class Euro365ResultsTest(unittest.TestCase):
    def test_finished_structured_result_uses_exact_event_id(self):
        artifact = results.build_artifact(data(prediction()), {"data": {"e1": {
            "home": 2, "away": 1, "status": "ended", "corners": 11,
        }}}, generated_at="2027-03-10T22:00:00Z")
        self.assertEqual(artifact["results"], [{
            "event_id": "e1", "time": "2027-03-10T18:00:00.000Z", "league": "League",
            "home": "Home", "away": "Away", "source": "Euro365", "status": "finished",
            "home_goals": 2, "away_goals": 1, "total_corners": 11, "evidence": "scores_endpoint",
        }])
        self.assertEqual(artifact["generated_at_utc"], "2027-03-10T22:00:00Z")

    def test_zero_scores_and_list_payload_are_supported(self):
        payload = [{"id": "e1", "score": {"home": 0, "away": 0}, "status": "finished"}]
        artifact = results.build_artifact(data(prediction()), payload)
        self.assertEqual((artifact["results"][0]["home_goals"], artifact["results"][0]["away_goals"]), (0, 0))

    def test_live_and_incomplete_results_are_not_final(self):
        for score in ({"home": 2, "away": 1, "status": "live"},
                      {"home": 2, "status": "ended"},
                      {"home": 2, "away": 1, "status": "unknown"}):
            self.assertEqual(results.build_artifact(data(prediction()), {"e1": score})["results"], [])

    def test_production_ft_fallback_and_corner_text(self):
        row = prediction(actualResult="ФТ · 1:1 · X · голов: 2 · угл.: 12")
        artifact = results.build_artifact(data(row), {})
        self.assertEqual(artifact["results"][0]["home_goals"], 1)
        self.assertEqual(artifact["results"][0]["away_goals"], 1)
        self.assertEqual(artifact["results"][0]["total_corners"], 12)
        self.assertEqual(artifact["results"][0]["evidence"], "production_snapshot")
        self.assertEqual(results.build_artifact(data(prediction(actualResult="LIVE · 1:1")), {})["results"], [])

    def test_postponed_cancelled_and_abandoned_have_no_score(self):
        for provider_status, expected in (("postponed", "postponed"), ("cancelled", "cancelled"),
                                          ("abandoned", "abandoned")):
            artifact = results.build_artifact(data(prediction()), {"e1": {"status": provider_status}})
            self.assertEqual(artifact["results"][0]["status"], expected)
            self.assertIsNone(artifact["results"][0]["home_goals"])

    def test_other_sources_other_days_and_missing_ids_are_excluded(self):
        rows = [prediction(source="Other"), prediction(eventId=""),
                prediction(time="2027-03-11T00:00:00Z")]
        artifact = results.build_artifact(data(*rows), {"e1": {"home": 1, "away": 0, "status": "ended"}})
        self.assertEqual(artifact["results"], [])

    def test_write_artifact_updates_sorted_public_index_without_financial_data(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            artifact = results.build_artifact(data(prediction()), {"e1": {"home": 1, "away": 0, "status": "ended"}},
                                              generated_at="2027-03-10T22:00:00Z")
            path = results.write_artifact(output, artifact)
            self.assertEqual(path.name, "2027-03-10.json")
            index = json.loads((output / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["days"], ["2027-03-10"])
            serialized = json.dumps(artifact)
            self.assertNotIn("amount", serialized)
            self.assertNotIn("bookmaker", serialized)

    def test_invalid_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            results.build_artifact({}, {})
        with self.assertRaises(ValueError):
            results.build_artifact({"date": "bad", "predictions": []}, {})


if __name__ == "__main__":
    unittest.main()
