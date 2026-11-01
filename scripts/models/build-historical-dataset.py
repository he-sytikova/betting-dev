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
 "T1":"Turkey Super Lig","G1":"Greece Super League"}
CORNERS={"E0","E1","E2","E3","SC0","D1","I1","SP1","F1","N1"}
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
 matches=[]; errors=[]; invalid=0
 for file in files:
  code=file.stem.split("_",1)[0].upper()
  if code not in LEAGUES:
   errors.append({"file":file.name,"reason":"unknown league code"});continue
  with file.open(encoding="utf-8-sig",newline="") as f:
   try:rows=list(csv.DictReader(f))
   except Exception as e:errors.append({"file":file.name,"reason":str(e)});continue
  accepted=0
  for r in rows:
   dt=date(r.get("Date"));h=(r.get("HomeTeam") or "").strip();a=(r.get("AwayTeam") or "").strip()
   hg,ag=num(r.get("FTHG")),num(r.get("FTAG"))
   if not dt or not h or not a or hg is None or ag is None:invalid+=1;continue
   m={"date":dt,"season":file.stem.split("_",1)[1] if "_" in file.stem else "",
      "league":code,"home":h,"away":a,"hg":hg,"ag":ag,
      "referee":(r.get("Referee") or "").strip()}
   for g,(hk,ak) in GROUPS.items():m[g+"_h"],m[g+"_a"]=num(r.get(hk)),num(r.get(ak))
   matches.append(m);accepted+=1
  errors.append({"file":file.name,"rows":len(rows),"accepted":accepted})
 matches.sort(key=lambda x:(x["league"],x["date"],x["home"],x["away"]))
 histories=defaultdict(lambda:{**{g:deque(maxlen=10) for g in GROUPS},"matches":deque(maxlen=10)})
 league_hist=defaultdict(lambda:deque(maxlen=100)); refs=defaultdict(lambda:deque(maxlen=20)); out=[];i=0
 while i<len(matches):
  league=matches[i]["league"];day=matches[i]["date"];j=i
  while j<len(matches) and matches[j]["league"]==league and matches[j]["date"]==day:j+=1
  pending=[]
  for m in matches[i:j]:
   hk,ak=(league,m["home"]),(league,m["away"])
   r={"date":day.isoformat(),"season":m["season"],"league":league,"home_team":m["home"],"away_team":m["away"],
      "home_matches_before":len(histories[hk]["matches"]),"away_matches_before":len(histories[ak]["matches"]),
      "league_matches_before":len(league_hist[(league,"matches")]),"home_advantage":1,
      "referee_available":int(bool(m["referee"])),"referee_matches_before":len(refs[(league,m["referee"])]) if m["referee"] else 0,
      "corners_model_eligible":int(league in CORNERS),"cards_model_eligible":int(league in CARDS)}
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
   r["corners_available"]=int(m["corners_h"] is not None and m["corners_a"] is not None)
   r["cards_available"]=int(m["cards_h"] is not None and m["cards_a"] is not None)
   r["corners_total"]=m["corners_h"]+m["corners_a"] if r["corners_available"] else ""
   r["cards_total"]=m["cards_h"]+m["cards_a"] if r["cards_available"] else ""
   out.append(r);pending.append(m)
  for m in pending:
   hk,ak=(league,m["home"]),(league,m["away"])
   histories[hk]["matches"].append(1);histories[ak]["matches"].append(1)
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
 if not out:raise SystemExit("No valid match rows found.")
 OUT.parent.mkdir(parents=True,exist_ok=True)
 with OUT.open("w",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=list(out[0].keys()));w.writeheader();w.writerows(out)
 by={}
 for c,n in LEAGUES.items():
  rs=[r for r in out if r["league"]==c]
  by[c]={"name":n,"rows":len(rs),"cornersRows":sum(r["corners_available"] for r in rs),
   "cardsRows":sum(r["cards_available"] for r in rs),"cornersEligible":c in CORNERS,"cardsEligible":c in CARDS,
   "from":min((r["date"] for r in rs),default=None),"through":max((r["date"] for r in rs),default=None)}
 report={"generatedAt":datetime.utcnow().isoformat()+"Z","sourceFiles":len(files),"rows":len(out),
  "invalidRows":invalid,"leagues":by,"files":errors,"output":str(OUT),
  "note":"Features use strictly prior match dates; all matches on a given date are added to histories only after that date is processed."}
 REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
 print(json.dumps({"rows":len(out),"sourceFiles":len(files),"report":str(REPORT)},indent=2))
if __name__=="__main__":main()
