#!/usr/bin/env python3
"""Build a leakage-safe match-level model dataset from local CSV files.

Place season/division CSVs in data/raw/football-data/. This script intentionally
does not fetch data from external providers. It creates data/corners_cards_training.csv
and data/historical_dataset_report.json for the experimental pipeline.
"""
from __future__ import annotations
import csv, json
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/"data"/"raw"/"football-data"
OUT=ROOT/"data"/"corners_cards_training.csv"
REPORT=ROOT/"data"/"historical_dataset_report.json"
LEAGUES={
 "E0":"England Premier League","E1":"England Championship","E2":"England League One","E3":"England League Two",
 "SC0":"Scotland Premiership","SC1":"Scotland Championship","SC2":"Scotland League One","SC3":"Scotland League Two",
 "D1":"Germany Bundesliga","D2":"Germany 2 Bundesliga","I1":"Italy Serie A","I2":"Italy Serie B",
 "SP1":"Spain La Liga","SP2":"Spain Segunda","F1":"France Ligue 1","F2":"France Ligue 2",
 "N1":"Netherlands Eredivisie","B1":"Belgium First Division","P1":"Portugal Primeira Liga",
 "T1":"Turkey Super Lig","G1":"Greece Super League",
 "RUS":"Russia Premier League","BRA":"Brazil Serie A","USA":"USA MLS",
 "NOR":"Norway Eliteserien","SWE":"Sweden Allsvenskan"}
# Results-only leagues come from single-file-per-country CSVs (football-data
# /new/{CODE}.csv) with a different schema (Home/Away/HG/AG, Season column) and
# no match statistics. They feed the goals model only; all stats-availability
# flags are zero for them, so corners/cards/fouls/shots models never see them.
RESULTS_ONLY={"RUS","BRA","USA","NOR","SWE"}
# Country family: clubs move between divisions of the same country (promotion/
# relegation), so rolling team histories are keyed by (family, team), not by
# (league, team). A promoted club keeps its second-division form instead of
# starting from zero history. League averages and referee histories stay per
# league on purpose.
FAMILY={
 "E0":"ENG","E1":"ENG","E2":"ENG","E3":"ENG",
 "SC0":"SCO","SC1":"SCO","SC2":"SCO","SC3":"SCO",
 "D1":"GER","D2":"GER","I1":"ITA","I2":"ITA",
 "SP1":"ESP","SP2":"ESP","F1":"FRA","F2":"FRA",
 "N1":"NED","B1":"BEL","P1":"POR","T1":"TUR","G1":"GRE",
 "RUS":"RUS","BRA":"BRA","USA":"USA","NOR":"NOR","SWE":"SWE"}
CORNERS={"E0","E1","E2","E3","SC0","SC1","D1","D2","I1","I2","SP1","SP2","F1","F2","B1","P1","T1","G1"}
CARDS=set(CORNERS)
GROUPS={"goals":("FTHG","FTAG"),"corners":("HC","AC"),"cards":("HY","AY"),
 "fouls":("HF","AF"),"shots":("HS","AS"),"shots_on_target":("HST","AST"),"red_cards":("HR","AR")}
PREFIX={"goals":"goal_team","corners":"team","cards":"card_team","fouls":"foul_team",
 "shots":"shots_team","shots_on_target":"shots_on_target_team","red_cards":"red_card_team"}

def num(v):
 try:
  x=float(str(v).strip()); return x if x>=0 else None
 except (ValueError,TypeError): return None

def date(v):
 for f in ("%d/%m/%y","%d/%m/%Y","%Y-%m-%d"):
  try:return datetime.strptime((v or "").strip(),f).date()
  except ValueError:pass
 return None

def avg(xs):return sum(xs)/len(xs) if xs else None

# Elo ratings (per country family) and exponentially time-decayed attack/defence
# goal ratings. Both are updated strictly after a match date is processed, so
# features never see same-day results.
ELO_START=1500.0;ELO_K=20.0;ELO_HFA=65.0
DECAY_HALFLIFE_DAYS=75.0

def elo_expected(rh,ra):
 return 1.0/(1.0+10.0**(-((rh+ELO_HFA)-ra)/400.0))

def margin_multiplier(diff):
 n=abs(int(diff))
 if n<=1:return 1.0
 if n==2:return 1.5
 return 1.5+(n-2)*0.25

def decayed_ratings(state):
 # state = [last_date, w_sum, gf_sum, ga_sum] already decayed to last_date;
 # the ratio gf/w is invariant to further common decay, so reading is O(1).
 if not state or state[1]<=1e-9:return (None,None)
 return (state[2]/state[1],state[3]/state[1])

def decayed_update(state,day,goals_for,goals_against):
 if state is None:return [day,1.0,goals_for,goals_against]
 f=0.5**((day-state[0]).days/DECAY_HALFLIFE_DAYS)
 return [day,state[1]*f+1.0,state[2]*f+goals_for,state[3]*f+goals_against]

def model_eligibility(league, corners_available, cards_available):
 return {
  "corners_model_eligible": int(league in CORNERS and bool(corners_available)),
  "cards_model_eligible": int(league in CARDS and bool(cards_available)),
 }

def features(hist,prefix):
 d={}
 for n in (5,10):
  xs=list(hist)[-n:]
  d[f"{prefix}_for_avg{n}"]=avg([x[0] for x in xs])
  d[f"{prefix}_against_avg{n}"]=avg([x[1] for x in xs])
  d[f"{prefix}_n{n}"]=len(xs)
 return d

def main():
 files=sorted(RAW.glob("*.csv"))
 if not files:raise SystemExit(f"No CSV files found in {RAW}. Add source files before running.")
 matches=[]; errors=[]; invalid=0; duplicates=0; seen=set()
 for file in files:
  code=file.stem.split("_",1)[0].upper()
  if code not in LEAGUES:
   errors.append({"file":file.name,"reason":"unknown league code"});continue
  with file.open(encoding="utf-8-sig",errors="replace",newline="") as f:
   try:
    reader=csv.DictReader(f)
    results_only=code in RESULTS_ONLY or (reader.fieldnames and "HomeTeam" not in reader.fieldnames)
    rows=list(reader)
   except Exception as e:errors.append({"file":file.name,"reason":str(e)});continue
  accepted=0; file_duplicates=0
  for r in rows:
   if results_only:
    h=(r.get("Home") or "").strip();a=(r.get("Away") or "").strip()
    hg,ag=num(r.get("HG")),num(r.get("AG"))
    season=(r.get("Season") or "").strip()
    referee=""
   else:
    h=(r.get("HomeTeam") or "").strip();a=(r.get("AwayTeam") or "").strip()
    hg,ag=num(r.get("FTHG")),num(r.get("FTAG"))
    season=file.stem.split("_",1)[1] if "_" in file.stem else ""
    referee=(r.get("Referee") or "").strip()
   dt=date(r.get("Date"))
   if not dt or not h or not a or hg is None or ag is None:invalid+=1;continue
   key=(code,dt.isoformat(),h.casefold(),a.casefold())
   if key in seen:
    duplicates+=1;file_duplicates+=1;continue
   seen.add(key)
   m={"date":dt,"season":season,
      "league":code,"home":h,"away":a,"hg":hg,"ag":ag,
      "referee":referee}
   for g,(hk,ak) in GROUPS.items():m[g+"_h"],m[g+"_a"]=num(r.get(hk)),num(r.get(ak))
   matches.append(m);accepted+=1
  errors.append({"file":file.name,"rows":len(rows),"accepted":accepted,"duplicates":file_duplicates})
 matches.sort(key=lambda x:(x["date"],x["league"],x["home"],x["away"]))
 histories=defaultdict(lambda:{**{g:deque(maxlen=10) for g in GROUPS},"matches":deque(maxlen=10)})
 league_hist=defaultdict(lambda:deque(maxlen=100)); refs=defaultdict(lambda:deque(maxlen=20)); i=0
 elo=defaultdict(lambda:ELO_START); decay={}
 # Streaming writer: rows are written as they are produced (no giant list in RAM);
 # per-league and global aggregates are accumulated on the fly for the report.
 OUT.parent.mkdir(parents=True,exist_ok=True)
 out_f=OUT.open("w",newline="",encoding="utf-8")
 writer=None;total_rows=0
 AGG_KEYS=("corners_available","cards_available","fouls_available","shots_available","sot_available",
  "corners_model_eligible","cards_model_eligible")
 def new_agg():return {"rows":0,**{k:0 for k in AGG_KEYS},"from":None,"through":None}
 agg=defaultdict(new_agg);tot=new_agg()
 while i<len(matches):
  day=matches[i]["date"];j=i
  while j<len(matches) and matches[j]["date"]==day:j+=1
  pending=[]
  for m in matches[i:j]:
   league=m["league"];family=FAMILY[league]
   hk,ak=(family,m["home"]),(family,m["away"])
   r={"date":day.isoformat(),"season":m["season"],"league":league,"home_team":m["home"],"away_team":m["away"],
      "home_matches_before":len(histories[hk]["matches"]),"away_matches_before":len(histories[ak]["matches"]),
      "league_matches_before":len(league_hist[(league,"matches")]),"home_advantage":1,
      "referee_available":int(bool(m["referee"])),"referee_matches_before":len(refs[(league,m["referee"])]) if m["referee"] else 0,
      "home_elo":round(elo[hk],3),"away_elo":round(elo[ak],3)}
   h_att,h_def=decayed_ratings(decay.get(hk));a_att,a_def=decayed_ratings(decay.get(ak))
   r["home_att_decay"]=round(h_att,5) if h_att is not None else None
   r["home_def_decay"]=round(h_def,5) if h_def is not None else None
   r["away_att_decay"]=round(a_att,5) if a_att is not None else None
   r["away_def_decay"]=round(a_def,5) if a_def is not None else None
   for g,p in PREFIX.items():
    r.update({"home_"+k:v for k,v in features(histories[hk][g],p).items()})
    r.update({"away_"+k:v for k,v in features(histories[ak][g],p).items()})
   for g in GROUPS:r["league_"+g+"_avg"]=avg(list(league_hist[(league,g)]))
   rh=list(refs[(league,m["referee"])]) if m["referee"] else []
   r["referee_yellows_avg10"]=avg([x[0] for x in rh if x[0] is not None])
   r["referee_red_cards_avg10"]=avg([x[1] for x in rh if x[1] is not None])
   r["referee_fouls_avg10"]=avg([x[2] for x in rh if x[2] is not None])
   r["target_home_goals"],r["target_away_goals"]=m["hg"],m["ag"];r["goals_total"]=m["hg"]+m["ag"]
   r["target_home_corners"],r["target_away_corners"]=m["corners_h"],m["corners_a"]
   r["target_home_yellow"],r["target_away_yellow"]=m["cards_h"],m["cards_a"]
   r["target_home_fouls"],r["target_away_fouls"]=m["fouls_h"],m["fouls_a"]
   r["target_home_shots"],r["target_away_shots"]=m["shots_h"],m["shots_a"]
   r["target_home_sot"],r["target_away_sot"]=m["shots_on_target_h"],m["shots_on_target_a"]
   r["corners_available"]=int(m["corners_h"] is not None and m["corners_a"] is not None)
   r["cards_available"]=int(m["cards_h"] is not None and m["cards_a"] is not None)
   r["fouls_available"]=int(m["fouls_h"] is not None and m["fouls_a"] is not None)
   r["shots_available"]=int(m["shots_h"] is not None and m["shots_a"] is not None)
   r["sot_available"]=int(m["shots_on_target_h"] is not None and m["shots_on_target_a"] is not None)
   # A row is trainable only when both sides of the target are present.
   r.update(model_eligibility(league, r["corners_available"], r["cards_available"]))
   r["corners_total"]=m["corners_h"]+m["corners_a"] if r["corners_available"] else ""
   r["cards_total"]=m["cards_h"]+m["cards_a"] if r["cards_available"] else ""
   r["fouls_total"]=m["fouls_h"]+m["fouls_a"] if r["fouls_available"] else ""
   r["shots_total"]=m["shots_h"]+m["shots_a"] if r["shots_available"] else ""
   r["sot_total"]=m["shots_on_target_h"]+m["shots_on_target_a"] if r["sot_available"] else ""
   if writer is None:
    writer=csv.DictWriter(out_f,fieldnames=list(r.keys()));writer.writeheader()
   writer.writerow(r);total_rows+=1
   a=agg[league];a["rows"]+=1;tot["rows"]+=1
   for k in AGG_KEYS:a[k]+=r[k];tot[k]+=r[k]
   d=r["date"]
   if a["from"] is None or d<a["from"]:a["from"]=d
   if a["through"] is None or d>a["through"]:a["through"]=d
   pending.append(m)
  for m in pending:
   league=m["league"];family=FAMILY[league]
   hk,ak=(family,m["home"]),(family,m["away"])
   histories[hk]["matches"].append(1);histories[ak]["matches"].append(1)
   rh,ra=elo[hk],elo[ak]
   exp_home=elo_expected(rh,ra)
   goal_diff=m["hg"]-m["ag"]
   score_home=1.0 if goal_diff>0 else (0.5 if goal_diff==0 else 0.0)
   mult=margin_multiplier(goal_diff)
   elo[hk]=rh+ELO_K*mult*(score_home-exp_home)
   elo[ak]=ra+ELO_K*mult*((1.0-score_home)-(1.0-exp_home))
   decay[hk]=decayed_update(decay.get(hk),day,m["hg"],m["ag"])
   decay[ak]=decayed_update(decay.get(ak),day,m["ag"],m["hg"])
   for g in GROUPS:
    hv,av=m[g+"_h"],m[g+"_a"]
    if hv is not None and av is not None:
     histories[hk][g].append((hv,av));histories[ak][g].append((av,hv));league_hist[(league,g)].append(hv+av)
   league_hist[(league,"matches")].append(1)
   if m["referee"]:
    y=m["cards_h"]+m["cards_a"] if m["cards_h"] is not None and m["cards_a"] is not None else None
    rc=m["red_cards_h"]+m["red_cards_a"] if m["red_cards_h"] is not None and m["red_cards_a"] is not None else None
    fouls=m["fouls_h"]+m["fouls_a"] if m["fouls_h"] is not None and m["fouls_a"] is not None else None
    refs[(league,m["referee"])].append((y,rc,fouls))
  i=j
 out_f.close()
 if writer is None:raise SystemExit("No valid match rows found.")
 by={}
 for c,n in LEAGUES.items():
  a=agg.get(c,new_agg());rs=a["rows"]
  by[c]={"name":n,"rows":rs,"cornersRows":a["corners_available"],
   "cardsRows":a["cards_available"],
   "cornersEligible":c in CORNERS,"cardsEligible":c in CARDS,
   "resultsOnly":c in RESULTS_ONLY,
   "cornersEligibleRows":a["corners_model_eligible"],
   "cardsEligibleRows":a["cards_model_eligible"],
   "cornersCoveragePct":round(100*a["corners_available"]/rs,2) if rs else 0,
   "cardsCoveragePct":round(100*a["cards_available"]/rs,2) if rs else 0,
   "foulsCoveragePct":round(100*a["fouls_available"]/rs,2) if rs else 0,
   "shotsCoveragePct":round(100*a["shots_available"]/rs,2) if rs else 0,
   "sotCoveragePct":round(100*a["sot_available"]/rs,2) if rs else 0,
   "from":a["from"],"through":a["through"]}
 report={"generatedAt":datetime.utcnow().isoformat()+"Z","sourceFiles":len(files),"rows":total_rows,
  "invalidRows":invalid,"duplicatesRemoved":duplicates,"leagues":by,"files":errors,"output":str(OUT),
  "cornersAvailableRows":tot["corners_available"],
  "cardsAvailableRows":tot["cards_available"],
  "cornersEligibleRows":tot["corners_model_eligible"],
  "cardsEligibleRows":tot["cards_model_eligible"],
  "note":"Features use strictly prior match dates; all matches on a given date (all leagues) are added to histories only after that date is processed. Team rolling histories, Elo ratings and time-decayed attack/defence goal ratings are keyed by country family, so clubs keep their form and ratings across promotion/relegation between divisions of the same country; league averages and referee histories remain per league. Elo: start 1500, K=20, home advantage 65 points, margin multiplier 1/1.5/1.5+0.25*(N-2). Decayed ratings use a 75-day half-life over goals for/against. Results-only leagues (RUS/BRA/USA/NOR/SWE, /new/ single-file CSVs) carry results only: all stats-availability flags are zero, so they train the goals model but never the stats models."}
 REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
 print(json.dumps({"rows":total_rows,"sourceFiles":len(files),"duplicatesRemoved":duplicates,"report":str(REPORT)},indent=2))
if __name__=="__main__":main()
