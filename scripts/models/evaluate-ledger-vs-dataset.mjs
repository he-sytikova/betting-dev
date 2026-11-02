// Evaluate captured ledger predictions against realised match statistics from
// the historical training dataset (football-data.co.uk actuals). Unlike the
// feed-based evaluator (evaluate-predictions.mjs, goals only), this scores
// every published market: 1X2, double chance, goal totals, team totals,
// corners, yellow cards, fouls, shots and shots on target.
//
// Matching: ledger fixtures are resolved through the same runtime modules the
// pages use (league aliases + team aliases), then joined to the dataset by
// (league, normalized teams, Moscow kickoff date +/- 1 day).
// Dedupe: per (model, fixture) keep the latest capture strictly before kickoff.
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const goalsRt = require("../../goals-internal-model.js");
const cornersRt = require("../../internal-model.js");
const statsRt = require("../../stats-internal-model.js");

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const LEDGER_DIR = path.join(ROOT, "data/prediction-ledger");
const DATASET = path.join(ROOT, "data/corners_cards_training.csv");
const OUTPUT = path.join(LEDGER_DIR, "evaluation-internal.json");

const RUNTIMES = {
  "goals": { rt: goalsRt, modelFile: "data/goals_markets_models.json" },
  "corners-cards": { rt: cornersRt, modelFile: "data/corners_cards_models.json" },
  "stats": { rt: statsRt, modelFile: "data/stats_markets_models.json" },
};

const DATASET_COLUMNS = [
  "target_home_goals", "target_away_goals", "goals_total",
  "corners_total", "cards_total", "fouls_total", "shots_total", "sot_total",
  "corners_available", "cards_available", "fouls_available", "shots_available", "sot_available",
];

async function loadDatasetIndex() {
  const rl = readline.createInterface({ input: fs.createReadStream(DATASET), crlfDelay: Infinity });
  let header = null;
  const index = new Map();
  for await (const line of rl) {
    if (!line) continue;
    const cols = line.split(",");
    if (!header) { header = cols; continue; }
    const at = name => header.indexOf(name);
    const get = name => cols[at(name)];
    const home = goalsRt.norm(get("home_team"));
    const away = goalsRt.norm(get("away_team"));
    if (!home || !away) continue;
    const row = {};
    for (const col of DATASET_COLUMNS) {
      const v = get(col);
      row[col] = v === "" || v === undefined ? null : Number(v);
    }
    index.set(`${get("date")}|${get("league")}|${home}|${away}`, row);
  }
  return index;
}

// Parse "LABEL: 12.3%" lines; tolerates trailing "/ ТМ: ..." and "(NB2)" suffixes.
function parseProbCell(text) {
  const out = new Map();
  for (const line of String(text || "").split("\n")) {
    const m = line.match(/^(.+?):\s*(\d+(?:[.,]\d+)?)\s*%/);
    if (m) out.set(m[1].trim(), Number(m[2].replace(",", ".")) / 100);
  }
  return out;
}

function totalsFromCell(text, pattern) {
  // Returns [{line, over}] from labels like "ТБ 2.5", "ТБ угл. 9.5", "ТБ ЖК 3.5".
  const probs = parseProbCell(text);
  const out = [];
  for (const [label, p] of probs) {
    const m = label.match(pattern);
    if (m) out.push({ line: Number(m[1]), over: p });
  }
  return out;
}

function metricsBucket() {
  return { n: 0, logLoss: 0, brier: 0, predicted: 0, observed: 0 };
}
function addBinary(bucket, p, event) {
  const pc = Math.min(Math.max(p, 1e-9), 1 - 1e-9);
  bucket.n += 1;
  bucket.logLoss += -(event * Math.log(pc) + (1 - event) * Math.log(1 - pc));
  bucket.brier += (pc - event) ** 2;
  bucket.predicted += pc;
  bucket.observed += event;
}
function finalize(bucket) {
  if (!bucket.n) return null;
  return {
    n: bucket.n,
    logLoss: +(bucket.logLoss / bucket.n).toFixed(5),
    brier: +(bucket.brier / bucket.n).toFixed(5),
    meanPredicted: +(bucket.predicted / bucket.n).toFixed(4),
    observedRate: +(bucket.observed / bucket.n).toFixed(4),
  };
}

function moscowDate(iso) {
  return new Date(new Date(iso).getTime() + 3 * 3600 * 1000).toISOString().slice(0, 10);
}
function shiftDay(dateStr, days) {
  const d = new Date(dateStr + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function lineBucket(container, market, line) {
  const m = (container[market] ||= { lines: {} });
  return (m.lines[String(line)] ||= metricsBucket());
}

function scoreTotals(bucket, perLeague, market, cellText, pattern, actual, available) {
  if (available === 0 || available === null || actual === null || actual === undefined || Number.isNaN(actual)) return;
  for (const { line, over } of totalsFromCell(cellText, pattern)) {
    const event = actual > line ? 1 : 0;
    addBinary(lineBucket(bucket, market, line), over, event);
    addBinary(lineBucket(perLeague, market, line), over, event);
  }
}

function scoreGoals(probCells, row, bucket, perLeague) {
  const outcomeCell = probCells.find(c => String(c).includes("П1"));
  const totalsCell = probCells.find(c => /^ТБ \d/m.test(String(c)) && !String(c).includes("ИТ"));
  const homeTeamCell = probCells.find(c => String(c).includes("ИТБ1"));
  const awayTeamCell = probCells.find(c => String(c).includes("ИТБ2"));
  const hg = row.target_home_goals, ag = row.target_away_goals;
  if (outcomeCell && hg !== null && ag !== null) {
    const probs = parseProbCell(outcomeCell);
    const p1 = probs.get("П1"), x = probs.get("X"), p2 = probs.get("П2");
    if (p1 !== undefined && x !== undefined && p2 !== undefined) {
      const sum = p1 + x + p2 || 1;
      const probsN = [p1 / sum, x / sum, p2 / sum];
      const actual = hg > ag ? 0 : (hg === ag ? 1 : 2);
      for (const bb of [(bucket["1x2"] ||= metricsBucket()), (perLeague["1x2"] ||= metricsBucket())]) {
        bb.n += 1;
        bb.logLoss += -Math.log(Math.max(probsN[actual], 1e-9));
        bb.brier += probsN.reduce((s, p, i) => s + (p - (i === actual ? 1 : 0)) ** 2, 0);
        bb.predicted += probsN[0];
        bb.observed += actual === 0 ? 1 : 0;
      }
      for (const [label, event] of [["1X", hg >= ag ? 1 : 0], ["12", hg !== ag ? 1 : 0], ["X2", hg <= ag ? 1 : 0]]) {
        const p = probs.get(label);
        if (p !== undefined) {
          addBinary((bucket[`dc_${label}`] ||= metricsBucket()), p, event);
          addBinary((perLeague[`dc_${label}`] ||= metricsBucket()), p, event);
        }
      }
    }
  }
  if (totalsCell) scoreTotals(bucket, perLeague, "goalTotals", totalsCell, /^ТБ (\d+(?:\.\d+)?)$/, row.goals_total, 1);
  if (homeTeamCell) scoreTotals(bucket, perLeague, "homeTeamGoals", homeTeamCell, /ИТБ1 (\d+(?:\.\d+)?)$/, hg, 1);
  if (awayTeamCell) scoreTotals(bucket, perLeague, "awayTeamGoals", awayTeamCell, /ИТБ2 (\d+(?:\.\d+)?)$/, ag, 1);
}

function scoreCornersCards(probCells, row, bucket, perLeague) {
  const cornersCell = probCells.find(c => String(c).includes("угл.")) ||
    probCells.find(c => totalsFromCell(c, /ТБ (\d+(?:\.\d+)?)/).some(x => x.line >= 7));
  const cardsCell = probCells.find(c => String(c).includes("ЖК")) ||
    probCells.find(c => c !== cornersCell && totalsFromCell(c, /ТБ (\d+(?:\.\d+)?)/).some(x => x.line <= 7));
  if (cornersCell) scoreTotals(bucket, perLeague, "corners", cornersCell, /ТБ (?:угл\. )?(\d+(?:\.\d+)?)/, row.corners_total, row.corners_available);
  if (cardsCell) scoreTotals(bucket, perLeague, "cards", cardsCell, /ТБ (?:ЖК )?(\d+(?:\.\d+)?)/, row.cards_total, row.cards_available);
}

function scoreStats(probCells, row, bucket, perLeague) {
  const [foulsCell, shotsCell, sotCell] = probCells;
  if (foulsCell) scoreTotals(bucket, perLeague, "fouls", foulsCell, /^ТБ (\d+(?:\.\d+)?)$/, row.fouls_total, row.fouls_available);
  if (shotsCell) scoreTotals(bucket, perLeague, "shots", shotsCell, /^ТБ (\d+(?:\.\d+)?)$/, row.shots_total, row.shots_available);
  if (sotCell) scoreTotals(bucket, perLeague, "sot", sotCell, /^ТБ (\d+(?:\.\d+)?)$/, row.sot_total, row.sot_available);
}

async function main() {
  const models = {};
  for (const cfg of Object.values(RUNTIMES)) {
    const file = path.join(ROOT, cfg.modelFile);
    if (fs.existsSync(file)) models[cfg.modelFile] = JSON.parse(fs.readFileSync(file, "utf8"));
  }
  const index = await loadDatasetIndex();

  const files = fs.existsSync(LEDGER_DIR)
    ? fs.readdirSync(LEDGER_DIR).filter(f => f.endsWith(".jsonl")).sort() : [];
  const records = [];
  for (const file of files) {
    for (const line of fs.readFileSync(path.join(LEDGER_DIR, file), "utf8").split("\n")) {
      if (!line.trim()) continue;
      try { records.push(JSON.parse(line)); } catch { /* skip malformed lines */ }
    }
  }

  // Dedupe: latest capture strictly before kickoff per (model, resolved fixture).
  const latest = new Map();
  for (const r of records) {
    if (!(r.capturedAt < r.kickoffAt)) continue;
    const cfg = RUNTIMES[r.model];
    const model = cfg && models[cfg.modelFile];
    const key = model
      ? r.model + "|" + cfg.rt.canonicalFixtureKey(model, r.league, r.home, r.away)
      : r.model + "|raw|" + r.home + "|" + r.away;
    const prev = latest.get(key);
    if (!prev || r.capturedAt > prev.capturedAt) latest.set(key, r);
  }
  const usable = [...latest.values()];

  const stats = {
    ledgerRecords: records.length, uniquePreKickoffFixtures: usable.length,
    unresolvedLeague: 0, unresolvedTeam: 0, noDatasetRow: 0, evaluated: 0,
  };
  const byModel = {};

  for (const r of usable) {
    const cfg = RUNTIMES[r.model];
    const model = cfg && models[cfg.modelFile];
    if (!cfg || !model) continue;
    const leagueCode = cfg.rt.resolveLeague(model, r.league);
    if (!leagueCode) { stats.unresolvedLeague++; continue; }
    const home = cfg.rt.resolveTeam(model, leagueCode, r.home);
    const away = cfg.rt.resolveTeam(model, leagueCode, r.away);
    if (!home || !away) { stats.unresolvedTeam++; continue; }
    let row = null;
    const baseDate = moscowDate(r.kickoffAt);
    for (const delta of [0, -1, 1]) {
      row = index.get(`${shiftDay(baseDate, delta)}|${leagueCode}|${home.key}|${away.key}`);
      if (row) break;
    }
    if (!row) { stats.noDatasetRow++; continue; }
    const bucket = (byModel[r.model] ||= {});
    const perLeague = ((bucket._byLeague ||= {})[leagueCode] ||= {});
    const probCells = (r.markets || []).filter(c => String(c).includes("%"));
    if (r.model === "goals") scoreGoals(probCells, row, bucket, perLeague);
    else if (r.model === "corners-cards") scoreCornersCards(probCells, row, bucket, perLeague);
    else if (r.model === "stats") scoreStats(probCells, row, bucket, perLeague);
    stats.evaluated++;
  }

  const finalizeBucket = b => b && b.lines
    ? Object.fromEntries(Object.entries(b.lines).map(([k, v]) => [k, finalize(v)]))
    : finalize(b);

  const report = {
    generatedAt: new Date().toISOString(),
    ledgerFiles: files,
    dataset: "data/corners_cards_training.csv",
    selectionPolicy: "latest capture strictly before kickoff per fixture; actuals from football-data.co.uk via the training dataset",
    coverage: stats,
    models: {},
  };
  for (const [modelKey, bucket] of Object.entries(byModel)) {
    const markets = {};
    for (const [name, b] of Object.entries(bucket)) {
      if (name === "_byLeague") continue;
      markets[name] = finalizeBucket(b);
    }
    const leagues = {};
    for (const [lg, lb] of Object.entries(bucket._byLeague || {})) {
      leagues[lg] = Object.fromEntries(Object.entries(lb).map(([name, b]) => [name, finalizeBucket(b)]));
    }
    report.models[modelKey] = { markets, byLeague: leagues };
  }
  fs.writeFileSync(OUTPUT, JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ ...stats, output: path.relative(ROOT, OUTPUT) }));
}

main().catch(err => { console.error(err); process.exit(1); });
