import unittest

from scripts.lines import acquire as acquisition
from scripts.lines.common import normalize_team


class FridayClubAliasTests(unittest.TestCase):
    def test_verified_bookmaker_variants_are_exact_identities(self):
        variants = [
            ("CR Vasco da Gama", "Vasco da Gama RJ"),
            ("Sport Club Internacional", "Internacional RS"),
            ("EC Vitoria Salvador", "Vitoria BA"),
            ("Red Bull Bragantino", "RB Bragantino"),
            ("Fluminense", "Fluminense RJ"),
            ("Orlando City", "Orlando City FC"),
            ("Cincinnati", "FC Cincinnati"),
            ("1. Magdeburg", "Magdeburg"),
            ("1. Heidenheim", "Heidenheim"),
            ("En Avant de Guingamp", "Guingamp"),
            ("Clermont Foot", "Clermont"),
            ("Rodez Aveyron", "Rodez"),
            ("Dijon FCO", "Dijon"),
            ("Red Star Saint-Ouen", "Red Star"),
            ("Bayern Munich", "Bayern"),
            ("1. Koln", "Köln"),
            ("Hellas Verona", "Verona"),
            ("Andorra Andorra la Vella", "FC Andorra"),
            ("Troyes AC", "Troyes"),
        ]
        for model_name, bookmaker_name in variants:
            with self.subTest(model_name=model_name, bookmaker_name=bookmaker_name):
                self.assertEqual(normalize_team(model_name), normalize_team(bookmaker_name))
        self.assertNotEqual(normalize_team("Bayern Munich"), normalize_team("Bayern Munich II"))
        self.assertNotEqual(normalize_team("Red Star"), normalize_team("Red Star Belgrade"))

    def test_alias_match_still_requires_the_time_window(self):
        fixture = {
            "home": "Bayern Munich", "away": "1. Koln",
            "time": "2026-11-20T19:30:00+00:00", "key": "bayern|koln",
        }
        line = {
            "bookmaker": "Fonbet", "match_id": "100", "team1": "Bayern", "team2": "Köln",
            "start_time": "2026-11-20T19:35:00+00:00", "odds": {}, "markets": {},
        }
        audit, selected = acquisition.match_lines([fixture], {"Fonbet": {"matches": [line]}})
        self.assertEqual(audit[0]["status"], "matched")
        self.assertEqual(len(selected), 1)

        line["start_time"] = "2026-11-20T19:51:00+00:00"
        audit, selected = acquisition.match_lines([fixture], {"Fonbet": {"matches": [line]}})
        self.assertEqual(audit[0]["status"], "no_line_match")
        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main()
