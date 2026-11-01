import fs from "node:fs";
import path from "node:path";

const ledgerDir = "data/prediction-ledger";
const data = JSON.parse(fs.readFileSync("data.json", "utf8"));
const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9а-я]+/gi, " ").trim().replace(/\s+/g, " ");
const dateKey = value => {
  const d = new Date(value);
  return Number.isFinite(d.getTime()) ? d.toISOString().slice(0,10) : "";
};
const results = new Map();
for (const item of (data.predictions || [])) {
  const match = String(item.actualResult || "").match(/(?:ФТ|FT)\s*[·|—-]*\s*(\d+)\s*:\s*(\d+)/i);
  if (!match) continue;
  const key = [dateKey(item.time), normalize(item.home), normalize(item.away)].join("|");
  if (!results.has(key)) results.set(key, {homeGoals:Number(match[1]),awayGoals:Number(match[2]),sources:[]});
  const entry = results.get(key);
  entry.sources.push(item.source);
}
const files = fs.readdirSync(ledgerDir).filter(name => /^\d{4}-\d{2}-\d{2}\.jsonl$/.test(name)).sort();
const scores = {oneXtwo:[], goalTotals:[]};
const unresolved = {noResult:0,cornersCardsAwaitingResultFeed:0,matched:0};
const evaluated = [];
function addBinary(bucket, probability, outcome, meta) {
  if (!Number.isFinite(probability) || probability < 0 || probability > 1) return;
  const p = Math.max(1e-6,Math.min(1-1e-6,probability));
  bucket.push({brier:(p-outcome)**2,logLoss:-(outcome*Math.log(p)+(1-outcome)*Math.log(1-p)),...meta});
}
for (const file of files) {
  const lines = fs.readFileSync(path.join(ledgerDir,file),"utf8").split("\n").filter(Boolean);
  for (const line of lines) {
    let forecast;
    try { forecast=JSON.parse(line); } catch { continue; }
    const key=[dateKey(forecast.kickoffAt),normalize(forecast.home),normalize(forecast.away)].join("|");
    const result=results.get(key);
    if (!result) { unresolved.noResult++; continue; }
    unresolved.matched++;
    const totalGoals=result.homeGoals+result.awayGoals;
    const row={model:forecast.model,kickoffAt:forecast.kickoffAt,league:forecast.league,home:forecast.home,away:forecast.away,score:result.homeGoals+":"+result.awayGoals,markets:[]};
    const cells=Array.isArray(forecast.cells)?forecast.cells:[];
    if(forecast.model==="goals" && cells.length>=4) {
      const outcomeCell=cells[3]||"";
      const probs={};
      for(const m of outcomeCell.matchAll(/(П1|П2|X)\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*%/g)) probs[m[1]]=Number(m[2].replace(",","."))/100;
      if(Number.isFinite(probs.П1)&&Number.isFinite(probs.X)&&Number.isFinite(probs.П2)){
        const actual=result.homeGoals>result.awayGoals?"П1":result.homeGoals<result.awayGoals?"П2":"X";
        const p=probs[actual];
        scores.oneXtwo.push({brier:Object.entries(probs).reduce((sum,[k,v])=>sum+(v-(k===actual?1:0))**2,0),logLoss:-Math.log(Math.max(1e-6,p)),league:forecast.league});
        row.markets.push({market:"1X2",actual,predicted:probs});
      }
      for(const cell of [cells[6] || ""]) {
        for(const m of String(cell).matchAll(/ТБ\s*([0-9]+(?:[.,][0-9]+)?)\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*%/g)){
          const line=Number(m[1].replace(",",".")), p=Number(m[2].replace(",","."))/100;
          const over=totalGoals>line?1:0;
          addBinary(scores.goalTotals,p,over,{line,league:forecast.league});
          row.markets.push({market:"goals_over_"+line,probability:p,actual:over});
        }
      }
    }
    if(forecast.model==="corners-cards") unresolved.cornersCardsAwaitingResultFeed++;
    if(row.markets.length) evaluated.push(row);
  }
}
const mean=(items,key)=>items.length?items.reduce((s,x)=>s+x[key],0)/items.length:null;
const summary={
 generatedAt:new Date().toISOString(),sourceDataGeneratedAt:data.generatedAt||null,
 ledgerFiles:files.length,ledgerRecords:files.reduce((n,f)=>n+fs.readFileSync(path.join(ledgerDir,f),"utf8").split("\n").filter(Boolean).length,0),
 matchedFinishedFixtures:unresolved.matched,unresolved,
 metrics:{
  oneXtwo:{n:scores.oneXtwo.length,brier:mean(scores.oneXtwo,"brier"),logLoss:mean(scores.oneXtwo,"logLoss")},
  goalTotals:{n:scores.goalTotals.length,brier:mean(scores.goalTotals,"brier"),logLoss:mean(scores.goalTotals,"logLoss")}
 },
 evaluated
};
fs.writeFileSync(path.join(ledgerDir,"evaluation.json"),JSON.stringify(summary,null,2)+"\n");
console.log(JSON.stringify({ledgerFiles:summary.ledgerFiles,ledgerRecords:summary.ledgerRecords,matched:unresolved.matched,metrics:summary.metrics,unresolved}));
