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
            # Champions League / Serie B / Championship audit 2026-11-25:
            # Latin source variants versus bookmaker display names and slugs.
            ("SK Slavia Praha", "Slavia Prague"),
            ("Sabah FK", "Sabah Baku"),
            ("FC Barcelona", "Barcelona"),
            ("Paris Saint-Germain", "PSG"),
            ("AS Roma", "Roma"),
            ("US Avellino 1912", "Avellino"),
            ("Calcio Padova", "Padova"),
            ("A.S.D. Atletico Arezzo", "Arezzo"),
            ("Viking FK", "Viking"),
            ("Club Brugge KV", "Brugge"),
            ("Liverpool FC", "Liverpool"),
            ("Internazionale Milano", "Inter Milan"),
            ("VfB Stuttgart", "Stuttgart"),
            ("LOSC Lille", "Lille OSC"),
            ("FC Bayern München", "Bayern Munich"),
            ("Sporting CP", "Sporting Lisbon"),
            ("Sporting Clube de Portugal", "Sporting Lisboa"),
            ("Atlético Mineiro", "Atletico MG"),
            ("Corinthians", "Corinthians SP"),
            ("West Bromwich Albion", "West Bromwich"),
            ("Birmingham City", "Birmingham"),
            # Russian display names (Winline unlinked events, Marathonbet).
            ("Sabah FK", "Сабах"),
            ("FC Barcelona", "Барселона"),
            ("SK Slavia Praha", "Славия Прага"),
            ("Villarreal", "Вильярреал"),
            ("Cremonese", "Кремонезе"),
            ("US Avellino 1912", "Авеллино"),
            ("Padova", "Падова"),
            ("Sampdoria", "Сампдория"),
            ("Mantova", "Мантова"),
            ("Arezzo", "Ареццо"),
            ("Pisa", "Пиза"),
            ("Modena", "Модена"),
            ("Watford", "Уотфорд"),
            ("Blackburn Rovers", "Блэкберн Роверс"),
            ("Burnley", "Бёрнли"),
            ("Birmingham City", "Бирмингем"),
            ("Bolton Wanderers", "Болтон Уондерерс"),
            ("West Bromwich Albion", "Вест Бромвич"),
            ("Stoke City", "Сток Сити"),
            ("Millwall", "Миллуолл"),
            ("Atlético Madrid", "Атлетико М"),
            ("Viking FK", "Викинг"),
            ("Club Brugge KV", "Брюгге"),
            ("Liverpool FC", "Ливерпуль"),
            ("Inter", "Интер М"),
            ("VfB Stuttgart", "Штутгарт"),
            ("Lille", "Лилль"),
            ("Bayern Munich", "Бавария"),
            ("Paris Saint-Germain", "ПСЖ"),
            ("AS Roma", "Рома"),
            ("Sporting CP", "Спортинг Л"),
            ("Sporting CP", "Спортинг Лиссабон"),
            ("Manchester United", "Манчестер Юнайтед"),
            ("Manchester United", "Манчестер Юнаитед"),
            ("Shakhtar Donetsk", "Шахтер Д"),
            ("Fenerbahçe", "Фенербахче"),
            ("Atlético Mineiro", "Атлетико Минейро"),
            ("Corinthians", "Коринтианс"),
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
