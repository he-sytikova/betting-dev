#!/usr/bin/env python3
"""Сборка обучающего датасета еврокубков (ЛЧ + Лига Европы + Лига Конференций).

Источники:
- матчи еврокубков: BSD (sports.bzzoiro.com API v2) — сырые JSON кэшируются
  локально через download-bsd-euro.py (токен только в env BSD_TOKEN, файлы не
  коммитятся). В отличие от football-data.org, BSD на бесплатном тарифе отдаёт
  все три турнира: ЛЧ (глубокая история), ЛЕ (с 2009), ЛК (с 2021). Берём
  сезоны с 2021 — раньше домашний датасет всё равно не покрывает.
  (Legacy-загрузчик download-fdo-euro.py остаётся для ЛЧ из
  football-data.org, но в пайплайне больше не используется.)
- домашние признаки команд: data/corners_cards_training.csv (уже построенный
  leakage-safe датасет чемпионатов) — для каждой команды берётся её состояние
  (Elo, затухающие рейтинги атаки/защиты, форма по голам) на день ПОСЛЕДНЕГО
  матча чемпионата строго до даты еврокубкового матча. Это консервативное
  приближение «признаков на дату» без утечки.

Кросс-страновые признаки (сравнимость стран):
- euro_elo: отдельный Elo, ведущийся по матчам ВСЕХ трёх турниров в общей
  хронологии (команда, вылетевшая из ЛЧ в ЛЕ, продолжает со своим рейтингом —
  рейтинг не теряется), старт 1500, K=24 с множителем разницы голов (как в
  домашнем Elo сборщика), между сезонами — регрессия 25% к 1500;
- euro_att/euro_def: затухающие (полураспад 75 дней) рейтинги голов в
  еврокубках относительно среднего еврокубкового;
- comp_is_el / comp_is_ecl: индикаторы турнира матча (ЛЧ — референсная
  категория), чтобы модель могла учесть систематическую разницу уровней.

Команды из стран без чемпионата в наших лигах (Австрия, Украина, Чехия и
т. п.) получают только euro-признаки; домашние признаки остаются пустыми и
помечаются индикаторами пропуска — как в основной модели.

Выход: CSV (локально, не коммитится) + печать отчёта о покрытии.
"""

import argparse
import csv
import json
import math
import unicodedata
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BSD_DIR = Path("/mnt/agents/work/data-bsd")
DEFAULT_DOMESTIC = ROOT / "data" / "corners_cards_training.csv"
DEFAULT_OUTPUT = ROOT / "data" / "euro_cups_training.csv"

ELO_START = 1500.0
ELO_K = 24.0
SEASON_REGRESS = 0.25          # доля возврата к 1500 между сезонами
DECAY_HALF_LIFE_DAYS = 75.0

# Турниры и префиксы файлов загрузчика. ЛЧ — референсная категория в
# comp-признаках (comp_is_el = comp_is_ecl = 0).
COMPETITIONS = {
    "CL": {"prefix": "bsd_cl", "name": "UEFA Champions League"},
    "EL": {"prefix": "bsd_el", "name": "UEFA Europa League"},
    "ECL": {"prefix": "bsd_ecl", "name": "UEFA Europa Conference League"},
}

BSD_TEAM_ALIASES = {
    # BSD name -> имя в домашнем датасете (football-data.co.uk).
    '1. FC Heidenheim': 'Heidenheim', '1. FC Köln': 'FC Koln', '1. FC Union Berlin': 'Union Berlin',
    '1. FSV Mainz 05': 'Mainz', 'AC Milan': 'Milan', 'AEK Athens': 'AEK',
    'AFC Ajax': 'Ajax', 'AS Monaco': 'Monaco', 'AS Roma': 'Roma',
    'AZ Alkmaar': 'AZ Alkmaar', 'Aberdeen': 'Aberdeen', 'Adana Demirspor': 'Ad. Demirspor',
    'Aris Thessaloniki': 'Aris', 'Arsenal': 'Arsenal', 'Aston Villa': 'Aston Villa',
    'Atalanta': 'Atalanta', 'Athletic Club': 'Ath Bilbao', 'Atlético Madrid': 'Ath Madrid',
    'Bayer 04 Leverkusen': 'Leverkusen', 'Başakşehir FK': 'Buyuksehyr', 'Benfica': 'Benfica',
    'Beşiktaş JK': 'Besiktas', 'Bologna': 'Bologna', 'Borussia Dortmund': 'Dortmund',
    'Bournemouth': 'Bournemouth', 'Brighton & Hove Albion': 'Brighton', 'Celta Vigo': 'Celta',
    'Celtic': 'Celtic', 'Cercle Brugge': 'Cercle Brugge', 'Chelsea': 'Chelsea',
    'Club Brugge KV': 'Club Brugge', 'Como': 'Como', 'Crystal Palace': 'Crystal Palace',
    'Dundee United': 'Dundee United', 'Eintracht Frankfurt': 'Ein Frankfurt', 'FC Arouca': 'Arouca',
    'FC Barcelona': 'Barcelona', 'FC Bayern München': 'Bayern Munich', 'FC Porto': 'Porto',
    'Fenerbahçe': 'Fenerbahce', 'Fiorentina': 'Fiorentina', 'Galatasaray': 'Galatasaray',
    'Getafe': 'Getafe', 'Gil Vicente': 'Gil Vicente', 'Girona FC': 'Girona',
    'Heart of Midlothian': 'Hearts', 'Hibernian': 'Hibernian', 'Inter': 'Inter',
    'Juventus': 'Juventus', 'KAA Gent': 'Gent', 'KRC Genk': 'Genk',
    'Kilmarnock': 'Kilmarnock', 'Konyaspor': 'Konyaspor', 'LOSC Lille': 'Lille',
    'Lazio': 'Lazio', 'Leicester City': 'Leicester', 'Lille': 'Lille',
    'Liverpool FC': 'Liverpool', 'Manchester City': 'Man City', 'Manchester United': 'Man United',
    'Molde FK': 'Molde', 'Motherwell': 'Motherwell', 'Nantes': 'Nantes',
    'Newcastle United': 'Newcastle', 'Nice': 'Nice', 'Nottingham Forest': "Nott'm Forest",
    'OFI Crete': 'OFI Crete', 'Olympiacos FC': 'Olympiakos', 'Olympique Lyonnais': 'Lyon',
    'Olympique de Marseille': 'Marseille', 'Osasuna': 'Osasuna', 'PAOK': 'PAOK',
    'Panathinaikos FC': 'Panathinaikos', 'Paris Saint-Germain': 'Paris SG', 'Paços de Ferreira': 'Pacos Ferreira',
    'RB Leipzig': 'RB Leipzig', 'RC Lens': 'Lens', 'RC Sporting Charleroi': 'Charleroi',
    'RC Strasbourg': 'Strasbourg', 'RSC Anderlecht': 'Anderlecht', 'Rangers': 'Rangers',
    'Rayo Vallecano': 'Vallecano', 'Real Betis': 'Betis', 'Real Madrid': 'Real Madrid',
    'Real Sociedad': 'Sociedad', 'Rosenborg BK': 'Rosenborg', 'Royal Antwerp FC': 'Antwerp',
    'Royale Union Saint-Gilloise': 'St. Gilloise', 'SC Freiburg': 'Freiburg', 'SK Brann': 'Brann',
    'SSC Napoli': 'Napoli', 'Samsunspor': 'Samsunspor', 'Santa Clara': 'Santa Clara',
    'Sevilla': 'Sevilla', 'Sint-Truidense VV': 'St Truiden', 'Sivasspor': 'Sivasspor',
    'Sporting Braga': 'Sp Braga', 'Sporting CP': 'Sp Lisbon', 'St. Johnstone': 'St Johnstone',
    'St. Mirren': 'St Mirren', 'Stade Brestois': 'Brest', 'Stade Rennais': 'Rennes',
    'Sunderland': 'Sunderland', 'TSG Hoffenheim': 'Hoffenheim', 'Tottenham Hotspur': 'Tottenham',
    'Toulouse': 'Toulouse', 'Trabzonspor': 'Trabzonspor', 'VfB Stuttgart': 'Stuttgart',
    'VfL Wolfsburg': 'Wolfsburg', 'Viking FK': 'Viking', 'Villarreal': 'Villarreal',
    'Vitória SC': 'Guimaraes', 'West Ham United': 'West Ham',
    # Вне покрытия чемпионатами (только euro-признаки):
    '1. FC Slovácko': None, 'AC Sparta Praha': None, 'AEK Larnaca': None, 'AEL Limassol': None,
    'AF Elbasani': None, 'AGF': None, 'AIK': None, 'AP Brera Strumica': None,
    'APOEL Nicosia': None, 'Anorthosis Famagusta': None, 'Apollon Limassol': None, 'Araz Naxçıvan': None,
    'Arda Kardzhali': None, 'Aris Limassol': None, 'Ashdod SC': None, 'Astana': None,
    'Atert Bissen': None, 'Atlètic Club Escaldes': None, 'B36 Tórshavn': None, 'BATE Borisov': None,
    'BFC Daugavpils': None, 'BK Häcken': None, 'BSC Young Boys': None, 'Bala Town': None,
    'Balzan F.C.': None, 'Baník Ostrava': None, 'Basel': None, 'Beitar Jerusalem': None,
    'Birkirkara FC': None, 'Bodø/Glimt': None, 'Bohemian FC': None, 'Bohemians Praha 1905': None,
    'Botev Plovdiv': None, 'Breidablik Kópavogur': None, 'Brøndby IF': None, 'CFR 1907 Cluj': None,
    'CS Fola Esch': None, 'CSKA Sofia': None, 'Caernarfon Town': None, 'Cherno More Varna': None,
    'Cliftonville FC': None, 'Coleraine FC': None, "Connah's Quay Nomads": None, 'Crusaders FC': None,
    'DAC 1904': None, 'Debreceni VSC': None, 'Derry City': None, 'Differdange FC 03': None,
    'Dinamo Batumi': None, 'Dinamo City': None, 'Dinamo Minsk': None, 'Dinamo Tbilisi': None,
    'Djurgårdens IF': None, 'Dukagjini': None, 'Dundalk FC': None, 'Dungannon Swifts': None,
    'Dynamo Brest': None, 'Dynamo Kyiv': None, 'ETO FC Győr': None, 'Europa FC': None,
    'F91 Dudelange': None, 'FC Alashkert': None, 'FC Ararat': None, 'FC Ararat-Armenia': None,
    "FC Bruno's Magpies": None, 'FC CSKA 1948 Sofia': None, 'FC Corvinul Hunedoara': None, 'FC Dila Gori': None,
    'FC Drita': None, 'FC Gagra': None, 'FC Haka': None, 'FC Hegelmann': None,
    'FC Honka': None, 'FC Hradec Králové': None, 'FC Iberia 1999': None, 'FC Isloch Minsk Region': None,
    'FC Kairat Almaty': None, 'FC Koper': None, 'FC Košice': None, 'FC Kryvbas Kryvyi Rih': None,
    'FC Kyzylzhar': None, 'FC København': None, 'FC Lausanne-Sport': None, 'FC Lugano': None,
    'FC Luzern': None, 'FC Malisheva': None, 'FC Midtjylland': None, 'FC Milsami Orhei': None,
    'FC Noah': None, 'FC Nordsjælland': None, 'FC Ordabasy': None, 'FC Petrocub Hîncesti': None,
    'FC Prishtina': None, 'FC Santa Coloma': None, 'FC Sfintul Gheorghe': None, 'FC Sion': None,
    'FC Sochi': None, 'FC Spaeri': None, 'FC Spartak Moscow': None, 'FC Spartak Trnava': None,
    'FC St. Gallen 1879': None, 'FC Struga Trim & Lum': None, 'FC Thun': None, 'FC Tobol': None,
    'FC Tobyl': None, 'FC Torpedo Kutaisi': None, 'FC Twente': None, 'FC Universitatea Cluj': None,
    'FC Utrecht': None, 'FC Vaduz': None, 'FC Viktoria Plzeň': None, 'FC Zimbru Chişinău': None,
    'FC Zürich': None, 'FCI Levadia Tallinn': None, 'FCSB': None, 'FCV Farul Constanța': None,
    'FH Hafnarfjörður': None, 'FK Aktobe': None, 'FK Arsenal Tivat': None, 'FK Auda': None,
    'FK Austria Wien': None, 'FK Banga Gargždai': None, 'FK Borac Banja Luka': None, 'FK Budućnost Podgorica': None,
    'FK Crvena zvezda': None, 'FK Dečić Tuzi': None, 'FK Iskra Danilovgrad': None, 'FK Jablonec': None,
    'FK Kauno Žalgiris': None, 'FK Liepaja': None, 'FK Mornar Bar': None, 'FK Novi Pazar': None,
    'FK Panevėžys': None, 'FK Partizan': None, 'FK Partizani': None, 'FK Podgorica': None,
    'FK Rabotnički Skopje': None, 'FK Radnički 1923': None, 'FK Radnički Niš': None, 'FK Sarajevo': None,
    'FK Sileks Kratovo': None, 'FK Sutjeska Nikšić': None, 'FK Sūduva Marijampolė': None, 'FK TSC Bačka Topola': None,
    'FK Transinvest': None, 'FK Tuzla City': None, 'FK Vardar Skopje': None, 'FK Velež Mostar': None,
    'FK Vojvodina': None, 'FK Čukarički': None, 'FK Žalgiris': None, 'FK Železničar Pančevo': None,
    'FK Željezničar': None, 'Ferencváros TC': None, 'Feyenoord': None, 'Flora Tallinn': None,
    'Floriana FC': None, 'Fredrikstad FK': None, 'GAIS': None, 'GKS Katowice': None,
    'GNK Dinamo Zagreb': None, 'Glentoran': None, 'Glentoran FC': None, 'Go Ahead Eagles': None,
    'Gomel': None, 'Górnik Zabrze': None, 'Gżira United FC': None, 'HB Tórshavn': None,
    'HJK': None, 'HNK Hajduk Split': None, 'HNK Rijeka': None, 'Hammarby IF': None,
    "Hapoel Be'er Sheva": None, 'Hapoel Tel Aviv': None, 'Haverfordwest County AFC': None, 'Hibernians': None,
    'HŠK Zrinjski Mostar': None, 'IF Elfsborg': None, 'IF Vestri': None, 'IFK Göteborg': None,
    'Ilves': None, "Inter Club d'Escaldes": None, 'Inter Turku': None, 'Jagiellonia Białystok': None,
    'KA Akureyri': None, 'KF Ballkani': None, 'KF Egnatia': None, 'KF Laçi': None,
    'KF Llapi': None, 'KF Shkupi': None, 'KF Shkëndija': None, 'KF Teuta Durrës': None,
    'KF Tirana': None, 'KF Vllaznia': None, 'KR Reykjavík': None, 'KS Lechia Gdańsk': None,
    'Kairat Almaty': None, 'Kalmar FF': None, 'Kecskemét TE': None, 'Kisvárda FC': None,
    'Klaksvíkar Ítróttarfelag': None, 'Kolos Kovalivka': None, 'Kuopion Palloseura': None, 'LASK': None,
    'LNZ Cherkasy': None, 'La Fiorita': None, 'Larne FC': None, 'Lech Poznań': None,
    'Legia Warszawa': None, 'Levski Sofia': None, 'Lillestrøm SK': None, 'Lincoln Red Imps': None,
    'Linfield': None, 'Linfield FC': None, 'Lokomotiv Moscow': None, 'Lokomotiv Plovdiv': None,
    'Ludogorets': None, 'MFK Ružomberok': None, 'ML Vitebsk': None, 'Maccabi Haifa': None,
    'Maccabi Netanya': None, 'Maccabi Petach Tikva': None, 'Maccabi Tel Aviv': None, 'Makedonija Gjorce Petrov': None,
    'Malmö FF': None, 'Marsaxlokk FC': None, 'Mjällby AIF': None, 'Mladá Boleslav': None,
    'Mons Calpe': None, 'Mosta FC': None, 'MŠK Žilina': None, 'NEC Nijmegen': None,
    'NK Aluminij Kidričevo': None, 'NK Bravo': None, 'NK Celje': None, 'NK Domžale': None,
    'NK Maribor': None, 'NK Olimpija Ljubljana': None, 'NK Osijek': None, 'NK Varaždin': None,
    'NK Široki Brijeg': None, 'NSÍ Runavík': None, 'Narva Trans': None, 'Neftçi PFK': None,
    'Neman Grodno': None, 'Newtown AFC': None, 'Nõmme Kalju': None, 'NŠ Mura': None,
    'OFK Petrovac': None, 'Oleksandria': None, 'Omonia Nicosia': None, 'PSV Eindhoven': None,
    'Pafos FC': None, 'Paide Linnameeskond': None, 'Paksi FC': None, 'Penybont': None,
    'Pogoń Szczecin': None, 'Polissya Zhytomyr': None, 'Progrès Niederkorn': None, 'Puskás Akadémia': None,
    'Pyunik Yerevan': None, 'Qarabağ FK': None, 'Qəbələ FK': None, 'RFS': None,
    'Racing FC Union Luxembourg': None, 'Raków Częstochowa': None, 'Randers FC': None, 'Red Bull Salzburg': None,
    'Riga FC': None, 'Rubin Kazan': None, 'SC Dnipro-1': None, 'SC Gjilani': None,
    'SJK': None, 'SK Rapid Wien': None, 'SK Sigma Olomouc': None, 'SK Slavia Praha': None,
    'SK Sturm Graz': None, 'SP Tre Fiori': None, 'SP Tre Penne': None, 'SS Cosmos': None,
    'SS Folgore / Falciano': None, 'SS Virtus': None, 'Sabah FK': None, 'Sepsi OSK Sfântu Gheorghe': None,
    'Servette FC': None, 'Shakhtar Donetsk': None, 'Shakhter Karagandy': None, 'Shakhtyor Soligorsk': None,
    'Shamrock Rovers': None, 'Shelbourne': None, 'Sheriff Tiraspol': None, 'Silkeborg IF': None,
    'Sliema Wanderers': None, 'Sligo Rovers': None, "St Joseph's FC": None, "St. Patrick's Athletic": None,
    'Stjarnan Garðabær': None, 'Sumqayıt FK': None, 'Swift Hesperange': None, 'Tallinna Kalev': None,
    'The New Saints': None, 'Tikveš Kavadarci': None, 'Torpedo-BelAZ Zhodino': None, 'Torreense': None,
    'Tromsø IL': None, 'UE Sant Julià': None, 'UE Santa Coloma': None, 'US Mondorf-Les-Bains': None,
    'Una Strassen': None, 'Universitatea Craiova': None, 'Urartu': None, 'VPS': None,
    'Valletta FC': None, 'Valmiera FC': None, 'Valur Reykjavík': None, 'Viborg FF': None,
    'Videoton FC Fehérvár': None, 'Vitesse': None, 'Vorskla Poltava': None, 'Vålerenga IF': None,
    'Víkingur Gøta': None, 'Víkingur Reykjavík': None, 'Wisła Kraków': None, 'Wolfsberger AC': None,
    'Yelimay Semey': None, 'Zalaegerszegi TE': None, 'Zenit St. Petersburg': None, 'Zirə FK': None,
    'Zoria Luhansk': None, 'Újpest': None, 'Ħamrun Spartans FC': None, 'Śląsk Wrocław': None,
    'Şamaxı FK': None, 'ŠK Slovan Bratislava': None, 'Šiauliai FA': None,
}

# Legacy-таблица имён football-data.org -> домашний датасет. В пайплайне
# больше не участвует; нужна экспортёру для вариантов имён фида FB_DATA.
FDO_TEAM_ALIASES = {
    "AC Milan": "Milan", "AFC Ajax": "Ajax", "AS Monaco FC": "Monaco",
    "AS Roma": "Roma", "Arsenal FC": "Arsenal", "Aston Villa FC": "Aston Villa",
    "Celtic FC": "Celtic", "Chelsea FC": "Chelsea", "Club Brugge KV": "Club Brugge",
    "FC Barcelona": "Barcelona", "FC Porto": "Porto", "Fenerbahçe SK": "Fenerbahce",
    "Galatasaray SK": "Galatasaray", "Girona FC": "Girona", "Juventus FC": "Juventus",
    "Liverpool FC": "Liverpool", "Newcastle United FC": "Newcastle",
    "Olympique de Marseille": "Marseille", "PAE AEK": "AEK",
    "PAE Olympiakos SFP": "Olympiakos", "RB Leipzig": "RB Leipzig",
    "Racing Club de Lens": "Lens", "Real Madrid CF": "Real Madrid",
    "Royal Antwerp FC": "Antwerp", "SSC Napoli": "Napoli", "Sevilla FC": "Sevilla",
    "VfB Stuttgart": "Stuttgart", "Viking FK": "Viking", "Villarreal CF": "Villarreal",
    "1. FC Union Berlin": "Union Berlin", "Atalanta BC": "Atalanta",
    "Athletic Club": "Ath Bilbao", "Bayer 04 Leverkusen": "Leverkusen",
    "Bologna FC 1909": "Bologna", "Borussia Dortmund": "Dortmund",
    "Club Atlético de Madrid": "Ath Madrid", "Como 1907": "Como",
    "Eintracht Frankfurt": "Ein Frankfurt", "FC Bayern München": "Bayern Munich",
    "FC Internazionale Milano": "Inter", "Feyenoord Rotterdam": "Feyenoord",
    "FK Bodø/Glimt": "Bodo/Glimt", "Lille OSC": "Lille",
    "Manchester City FC": "Man City", "Manchester United FC": "Man United",
    "PSV": "PSV Eindhoven", "Paris Saint-Germain FC": "Paris SG",
    "Real Betis Balompié": "Betis", "Real Sociedad de Fútbol": "Sociedad",
    "Royale Union Saint-Gilloise": "St. Gilloise", "SS Lazio": "Lazio",
    "Sport Lisboa e Benfica": "Benfica", "Sporting Clube de Braga": "Sp Braga",
    "Sporting Clube de Portugal": "Sp Lisbon", "Stade Brestois 29": "Brest",
    "Tottenham Hotspur FC": "Tottenham",
    # ЛЕ/ЛК: команды чемпионатов из нашего покрытия.
    "Rangers FC": "Rangers", "Olympique Lyonnais": "Lyon", "OGC Nice": "Nice",
    "Toulouse FC": "Toulouse", "Stade Rennais FC 1901": "Rennes",
    "VfL Wolfsburg": "Wolfsburg", "SC Freiburg": "Freiburg",
    "TSG 1899 Hoffenheim": "Hoffenheim", "1. FSV Mainz 05": "Mainz",
    "1. FC Heidenheim 1846": "Heidenheim", "ACF Fiorentina": "Fiorentina",
    "AZ Alkmaar": "AZ Alkmaar", "Vitória SC": "Guimaraes",
    "RSC Anderlecht": "Anderlecht", "KRC Genk": "Genk", "KAA Gent": "Gent",
    "Standard Liège": "Standard", "Molde FK": "Molde", "Rosenborg BK": "Rosenborg",
    "SK Brann": "Brann", "PAOK FC": "PAOK", "Panathinaikos FC": "Panathinaikos",
    "Beşiktaş JK": "Besiktas", "Trabzonspor": "Trabzonspor",
    "İstanbul Başakşehir FK": "Buyuksehyr", "Sivasspor": "Sivasspor",
    # Вне покрытия чемпионатами (только euro-признаки):
    "AC Sparta Praha": None, "BSC Young Boys": None, "FC København": None,
    "FC Red Bull Salzburg": None, "FK Crvena Zvezda": None, "FK Kairat": None,
    "FK Shakhtar Donetsk": None, "GNK Dinamo Zagreb": None, "LASK Linz": None,
    "Paphos FC": None, "Qarabağ Ağdam FK": None, "Sabah FK": None,
    "SK Slavia Praha": None, "SK Sturm Graz": None, "ŠK Slovan Bratislava": None,
}

# Явный кросс-волк euro-only имён BSD -> имён football-data.org (для
# вариантов фида FB_DATA в экспортёте; на канонические имена не влияет).
BSD_TO_FDO = {
    "AC Sparta Praha": "AC Sparta Praha", "BSC Young Boys": "BSC Young Boys",
    "FC København": "FC København", "FK Crvena zvezda": "FK Crvena Zvezda",
    "GNK Dinamo Zagreb": "GNK Dinamo Zagreb", "Kairat Almaty": "FK Kairat",
    "LASK": "LASK Linz", "Pafos FC": "Paphos FC", "Qarabağ FK": "Qarabağ Ağdam FK",
    "Red Bull Salzburg": "FC Red Bull Salzburg", "Sabah FK": "Sabah FK",
    "Shakhtar Donetsk": "FK Shakhtar Donetsk", "SK Slavia Praha": "SK Slavia Praha",
    "SK Sturm Graz": "SK Sturm Graz", "ŠK Slovan Bratislava": "ŠK Slovan Bratislava",
}

# Одна и та же euro-only команда в разных сезонах BSD записана по-разному —
# схлопываем в одну каноническую идентичность (иначе Elo считал бы их
# разными командами). Только euro-only; проверено, что это один клуб.
CANONICAL_SYNONYMS = {
    "FC Kairat Almaty": "Kairat Almaty",
    "FK Podgorica": "FK Budućnost Podgorica",
    "Glentoran FC": "Glentoran",
    "Linfield FC": "Linfield",
}

DOMESTIC_FEATURES = ["elo", "att_decay", "def_decay",
                     "goal_for_avg5", "goal_against_avg5",
                     "goal_for_avg10", "goal_against_avg10"]

COMP_FEATURES = ["comp_is_el", "comp_is_ecl"]


def norm_name(value: str) -> str:
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def canonical_name(bsd_name: str) -> str:
    """Каноническое имя команды: имя в домашнем датасете, если чемпионат
    покрыт, иначе само BSD-имя (euro-only, через CANONICAL_SYNONYMS).
    Именно каноническими именами ключуются euro Elo и рейтинги."""
    dom = BSD_TEAM_ALIASES[bsd_name]
    if dom:
        return dom
    return CANONICAL_SYNONYMS.get(bsd_name, bsd_name)


def load_euro_matches(bsd_dir: Path) -> list:
    """Читает все файлы bsd_<comp>_<year>.json трёх турниров и возвращает
    завершённые матчи в общей хронологии; у каждого матча — поле comp и
    канонические имена home/away. Команды вне BSD_TEAM_ALIASES не маппятся
    (canonical = BSD-имя) — main() упадёт с понятным списком таких имён."""
    def canonical_or_self(bsd_name: str) -> str:
        return canonical_name(bsd_name) if bsd_name in BSD_TEAM_ALIASES else bsd_name

    matches = []
    for comp, spec in COMPETITIONS.items():
        for path in sorted(bsd_dir.glob(f"{spec['prefix']}_*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for m in data.get("matches", []):
                matches.append({
                    "date": pd.Timestamp(m["date"]),
                    "season": str(m["season"]),
                    "comp": comp,
                    "stage": m.get("stage") or "",
                    "home_bsd": m["home"],
                    "away_bsd": m["away"],
                    "home": canonical_or_self(m["home"]),
                    "away": canonical_or_self(m["away"]),
                    "home_goals": int(m["home_goals"]),
                    "away_goals": int(m["away_goals"]),
                })
    matches.sort(key=lambda r: r["date"])
    return matches


def domestic_state_series(domestic_csv: Path) -> dict:
    """Для каждой команды — хронологический список (date, state) её признаков
    ПОСЛЕ каждого сыгранного матча чемпионата. Состояние на дату еврокубкового
    матча — последняя запись строго до этой даты (послематчевое состояние
    предыдущего дня = доматчевое для следующего)."""
    df = pd.read_csv(domestic_csv, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].notna()].sort_values("date")
    series = defaultdict(list)
    for row in df.itertuples(index=False):
        d = row.date
        series[norm_name(row.home_team)].append((d, {
            "elo": row.home_elo, "att_decay": row.home_att_decay, "def_decay": row.home_def_decay,
            "goal_for_avg5": row.home_goal_team_for_avg5, "goal_against_avg5": row.home_goal_team_against_avg5,
            "goal_for_avg10": row.home_goal_team_for_avg10, "goal_against_avg10": row.home_goal_team_against_avg10,
        }))
        series[norm_name(row.away_team)].append((d, {
            "elo": row.away_elo, "att_decay": row.away_att_decay, "def_decay": row.away_def_decay,
            "goal_for_avg5": row.away_goal_team_for_avg5, "goal_against_avg5": row.away_goal_team_against_avg5,
            "goal_for_avg10": row.away_goal_team_for_avg10, "goal_against_avg10": row.away_goal_team_against_avg10,
        }))
    return series


def state_before(series_list: list, when: pd.Timestamp):
    """Последнее состояние строго до даты (бинпоиск по отсортированным датам)."""
    import bisect
    dates = [d for d, _ in series_list]
    i = bisect.bisect_left(dates, when)
    return series_list[i - 1][1] if i > 0 else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bsd-dir", type=Path, default=DEFAULT_BSD_DIR)
    parser.add_argument("--domestic", type=Path, default=DEFAULT_DOMESTIC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    matches = load_euro_matches(args.bsd_dir)
    if not matches:
        raise SystemExit(f"No finished euro-cup matches in {args.bsd_dir}; run download-bsd-euro.py first")
    per_comp = {comp: sum(1 for m in matches if m["comp"] == comp) for comp in COMPETITIONS}
    print(f"Euro-cup matches loaded: {len(matches)} {per_comp} "
          f"({min(m['date'] for m in matches).date()} .. {max(m['date'] for m in matches).date()})")

    unknown = sorted(({m["home_bsd"] for m in matches} | {m["away_bsd"] for m in matches})
                     - set(BSD_TEAM_ALIASES))
    if unknown:
        raise SystemExit(f"Unmapped BSD teams (extend BSD_TEAM_ALIASES): {unknown}")

    series = domestic_state_series(args.domestic)
    covered = sum(1 for v in BSD_TEAM_ALIASES.values() if v)
    print(f"BSD teams: {len(BSD_TEAM_ALIASES)}, with domestic coverage: {covered}")

    rows, states, coverage, avg_goals = process_matches(matches, series)
    out = pd.DataFrame(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(f"written {len(out)} rows -> {args.output}")
    print("domestic coverage:", coverage)
    print("euro-cup avg goals/match:", round(avg_goals, 3))


def process_matches(matches, series):
    """Прогоняет матчи всех турниров по общей хронологии: считает признаки без
    утечки и возвращает (rows, final_states, coverage, avg_goals), где
    final_states — euro-состояния команд после последнего матча (для
    рантайм-экспорта). Elo и затухающие рейтинги единые для всех турниров:
    переход команды из ЛЧ в ЛЕ/ЛК рейтинг не обнуляет."""
    # --- Кросс-страновой Elo и затухающие euro-рейтинги по самим еврокубкам ---
    euro_elo = defaultdict(lambda: ELO_START)
    euro_gf = defaultdict(float)   # затухающая сумма забитых
    euro_ga = defaultdict(float)   # затухающая сумма пропущенных
    euro_w = defaultdict(float)    # затухающий вес
    euro_last = {}
    total_goals, total_matches = 0.0, 0
    seen_seasons = set()

    rows, coverage = [], {"both": 0, "home_only": 0, "away_only": 0, "none": 0}
    for m in matches:
        # межсезонная регрессия Elo к среднему
        if m["season"] not in seen_seasons:
            seen_seasons.add(m["season"])
            for t in list(euro_elo):
                euro_elo[t] = ELO_START + (euro_elo[t] - ELO_START) * (1 - SEASON_REGRESS)
            euro_gf.clear(); euro_ga.clear(); euro_w.clear(); euro_last.clear()

        h, a = m["home"], m["away"]
        days = lambda t: (m["date"] - euro_last[t]).days if t in euro_last else None

        def euro_rating(store, team):
            d = days(team)
            w_prev = euro_w[team] * (0.5 ** (d / DECAY_HALF_LIFE_DAYS)) if d is not None else 0.0
            s_prev = store[team] * (0.5 ** (d / DECAY_HALF_LIFE_DAYS)) if d is not None else 0.0
            return (s_prev / w_prev) if w_prev > 1e-9 else None

        feat = {
            "date": m["date"].date().isoformat(), "season": m["season"],
            "comp": m["comp"], "stage": m["stage"],
            "home_team": h, "away_team": a,
            "comp_is_el": 1 if m["comp"] == "EL" else 0,
            "comp_is_ecl": 1 if m["comp"] == "ECL" else 0,
            "home_euro_elo": euro_elo[h], "away_euro_elo": euro_elo[a],
            "home_euro_games": int(round(euro_w[h])) if h in euro_last else 0,
            "away_euro_games": int(round(euro_w[a])) if a in euro_last else 0,
            "home_euro_gf": euro_rating(euro_gf, h), "home_euro_ga": euro_rating(euro_ga, h),
            "away_euro_gf": euro_rating(euro_gf, a), "away_euro_ga": euro_rating(euro_ga, a),
            "target_home_goals": m["home_goals"], "target_away_goals": m["away_goals"],
        }
        # домашние признаки (состояние строго до даты матча)
        for side, team in (("home", h), ("away", a)):
            # каноническое имя euro-only команды отсутствует в домашнем
            # датасете — домашних признаков нет
            has_domestic = (BSD_TEAM_ALIASES[m[f"{side}_bsd"]] is not None)
            st = state_before(series.get(norm_name(team), []), m["date"]) if has_domestic else None
            if st:
                for k in DOMESTIC_FEATURES:
                    feat[f"{side}_dom_{k}"] = st[k]
                feat[f"{side}_dom_missing"] = 0
            else:
                for k in DOMESTIC_FEATURES:
                    feat[f"{side}_dom_{k}"] = None
                feat[f"{side}_dom_missing"] = 1
        key = (feat["home_dom_missing"], feat["away_dom_missing"])
        coverage[{ (0,0):"both",(0,1):"home_only",(1,0):"away_only",(1,1):"none" }[key]] += 1

        # обновление euro-истории ПОСЛЕ фиксации признаков (без утечки)
        eh, ea = euro_elo[h], euro_elo[a]
        exp_h = 1.0 / (1.0 + 10 ** (-((eh + 100) - ea) / 400.0))  # +100 — поле
        gd = abs(m["home_goals"] - m["away_goals"])
        mult = 1.0 if gd <= 1 else (1.5 if gd == 2 else (11 + gd) / 8.0)
        delta = ELO_K * mult * ((1.0 if m["home_goals"] > m["away_goals"] else 0.5 if m["home_goals"] == m["away_goals"] else 0.0) - exp_h)
        euro_elo[h] = eh + delta
        euro_elo[a] = ea - delta
        for team, gf, ga in ((h, m["home_goals"], m["away_goals"]), (a, m["away_goals"], m["home_goals"])):
            d = days(team)
            decay = 0.5 ** (d / DECAY_HALF_LIFE_DAYS) if d is not None else 0.0
            euro_gf[team] = euro_gf[team] * decay + gf
            euro_ga[team] = euro_ga[team] * decay + ga
            euro_w[team] = euro_w[team] * decay + 1.0
            euro_last[team] = m["date"]
        total_goals += m["home_goals"] + m["away_goals"]
        total_matches += 1
        rows.append(feat)

    final_states = {
        team: {
            "euroElo": euro_elo[team],
            "euroGf": (euro_gf[team] / euro_w[team]) if euro_w[team] > 1e-9 else None,
            "euroGa": (euro_ga[team] / euro_w[team]) if euro_w[team] > 1e-9 else None,
            "euroGames": sum(1 for m in matches if m["home"] == team or m["away"] == team),
            "euroLastDate": euro_last[team].date().isoformat() if team in euro_last else None,
        }
        for team in {m["home"] for m in matches} | {m["away"] for m in matches}
    }
    avg_goals = total_goals / total_matches if total_matches else 0.0
    return rows, final_states, coverage, avg_goals


if __name__ == "__main__":
    main()
