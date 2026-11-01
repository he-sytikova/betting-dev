#!/usr/bin/env python3
"""Evaluate archived internal forecasts against completed football-data.co.uk results."""
import csv, json, math, re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
LEDGER=ROOT/"data"/"prediction-ledger"
RAW=ROOT/"data"/"raw"/"football-data"
TEAM_ALIASES={
 "man utd":"manchester united","man united":"manchester united",
 "man city":"manchester city","spurs":"tottenham","wolves":"wolverhampton",
 "newcastle utd":"newcastle","brighton and hove albion":"brighton",
 "inter":"internazionale","ac milan":"milan","paris saint germain":"paris saint-germain",
 "psg":"paris saint-germain","ath madrid":"atletico madrid",
 "ath bilbao":"athletic bilbao","real sociedad":"real sociedad",
}
def norm(s):
 s=re.sub(r"[^a-z0-9]+"," ",(s or "").lower()).strip()
 return TEAM_ALIASES.get(s,s)
def parse_date(s):
 for fmt in ("%d/%m/%y","%d/%m/%Y","%Y-%m-%d"):
  try:return datetime.strptime((s or "").strip(),fmt).date().isoformat()
  except ValueError:pass
 return ""
def parse_kickoff(s):
 try:return datetime.fromisoformat(s.replace("Z","+00:00")).date().isoformat()
 except Exception:return ""
def avg(xs):return sum(xs)/len(xs) if xs else None
def safe_log(p):return -math.log(max(1e-6,min(1-1e-6,p)))
def add_binary(target,p,y,league,line):
 if 0<=p<=1:
  p=max(1e-6,min(1-1e-6,p))
  target.append({"brier":(p-y)**2,"logLoss":-(y*math.log(p)+(1-y)*math.log(1-p)),"league":league,"line":line})
def main():
 results={}
 files=list(RAW.glob("*.csv"))
 for file in files:
  with file.open(encoding="utf-8-sig",newline="") as f:
   for row in csv.DictReader(f):
    day=parse_date(row.get("Date")); h=norm(row.get("HomeTeam")); a=norm(row.get("AwayTeam"))
    if not day or not h or not a:continue
    def number(k):
     try:return float(row[k])
     except (KeyError,TypeError,ValueError):return None
    hg,ag=number("FTHG"),number("FTAG")
    if hg is None or ag is None:continue
    rec={"homeGoals":hg,"awayGoals":ag,"corners":None,"cards":None}
    hc,ac=number("HC"),number("AC")
    hy,ay=number("HY"),number("AY")
    if hc is not None and ac is not None:rec["corners"]=hc+ac
    if hy is not None and ay is not None:rec["cards"]=hy+ay
    results[(day,h,a)]=rec
 metrics=defaultdict(list); evaluated=[]; unmatched=[]; ledger_count=0; matched=0
 for file in sorted(LEDGER.glob("*.jsonl")):
  for line in file.read_text(encoding="utf-8").splitlines():
   if not line.strip():continue
   try:fc=json.loads(line)
   except json.JSONDecodeError:continue
   ledger_count+=1
   key=(parse_kickoff(fc.get("kickoffAt","")),norm(fc.get("home")),norm(fc.get("away")))
   result=results.get(key)
   if not result:
    unmatched.append({"model":fc.get("model"),"date":key[0],"home":fc.get("home"),"away":fc.get("away")});continue
   matched+=1; cells=[str(x) for x in fc.get("cells",[])]
   row={"model":fc.get("model"),"date":key[0],"league":fc.get("league"),"home":fc.get("home"),"away":fc.get("away"),"score":f'{int(result["homeGoals"])}:{int(result["awayGoals"])}',"markets":[]}
   if fc.get("model")=="goals":
    alltext="\n".join(cells)
    probs={}
    for m in re.finditer(r"(П1|П2|X)\s*:?\s*([0-9]+(?:[.,][0-9]+)?)\s*%",alltext):
     probs[m.group(1)]=float(m.group(2).replace(",","."))/100
    if all(k in probs for k in ("П1","X","П2")):
     actual="П1" if result["homeGoals"]>result["awayGoals"] else "П2" if result["homeGoals"]<result["awayGoals"] else "X"
     p=probs[actual]; brier=sum((v-(k==actual))**2 for k,v in probs.items())
     metrics["oneXtwo"].append({"brier":brier,"logLoss":safe_log(p),"league":fc.get("league")})
     row["markets"].append({"market":"1X2","actual":actual,"probabilities":probs})
    seen=set()
    for m in re.finditer(r"ТБ\s*([0-9]+(?:[.,][0-9]+)?)\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*%",alltext):
     threshold=float(m.group(1).replace(",",".")); p=float(m.group(2).replace(",","."))/100
     if threshold in seen:continue
     seen.add(threshold); actual=int(result["homeGoals"]+result["awayGoals"]>threshold)
     add_binary(metrics["goalTotals"],p,actual,fc.get("league"),threshold)
     row["markets"].append({"market":f"goals_over_{threshold}","probability":p,"actual":actual})
   elif fc.get("model")=="corners-cards":
    for market,idx,result_key in (("corners",5,"corners"),("cards",6,"cards")):
     actual_total=result.get(result_key)
     if idx>=len(cells) or actual_total is None:continue
     textcell=cells[idx]
     for m in re.finditer(r"ТБ\s*([0-9]+(?:[.,][0-9]+)?)\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*%",textcell):
      threshold=float(m.group(1).replace(",",".")); p=float(m.group(2).replace(",","."))/100
      outcome=int(actual_total>threshold)
      add_binary(metrics[market],p,outcome,fc.get("league"),threshold)
      row["markets"].append({"market":f"{market}_over_{threshold}","probability":p,"actual":outcome,"actualTotal":actual_total})
   if row["markets"]:evaluated.append(row)
 def summarize(xs):
  return {"n":len(xs),"brier":avg([x["brier"] for x in xs]),"logLoss":avg([x["logLoss"] for x in xs])}
 by_league={}
 for market,items in metrics.items():
  for league in sorted(set(x["league"] for x in items)):
   by_league.setdefault(market,{})[league]=summarize([x for x in items if x["league"]==league])
 report={"generatedAt":datetime.utcnow().isoformat()+"Z","source":"football-data.co.uk historical CSVs","sourceFiles":len(files),"ledgerFiles":len(list(LEDGER.glob("*.jsonl"))),"ledgerRecords":ledger_count,"matchedFixtures":matched,"unmatchedFixtures":unmatched,"metrics":{k:summarize(v) for k,v in metrics.items()},"byLeague":by_league,"evaluated":evaluated,"limitations":"Evaluation is available only where a completed fixture matches the archived prediction and the historical CSV includes the relevant market statistics. It is an offline monitoring sample, not evidence of profitability."}
 (LEDGER/"evaluation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps({"ledgerRecords":ledger_count,"matchedFixtures":matched,"metrics":report["metrics"],"unmatched":len(unmatched),"sourceFiles":len(files)},ensure_ascii=False))
if __name__=="__main__":main()
