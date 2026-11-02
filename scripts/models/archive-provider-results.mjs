import fs from "node:fs";
import path from "node:path";

const INPUT = "data.json";
const OUTPUT_DIR = "data/results-ledger";
const input = JSON.parse(fs.readFileSync(INPUT, "utf8"));
if (!Array.isArray(input.predictions)) throw new Error("data.json has no predictions[]");

function parseFinalResult(value) {
  const text = String(value || "").trim();
  if (!/^(?:ФТ|FT|FULL TIME|FINISHED|COMPLETED)\b/i.test(text)) return null;
  const score = text.match(/(?:ФТ|FT|FULL TIME|FINISHED|COMPLETED)\s*[·:-]?\s*(\d+)\s*[:–-]\s*(\d+)/i);
  if (!score) return null;
  const corners = text.match(/угл\.?\s*:\s*(\d+)/i);
  return {
    homeGoals: Number(score[1]),
    awayGoals: Number(score[2]),
    totalGoals: Number(score[1]) + Number(score[2]),
    result: Number(score[1]) > Number(score[2]) ? "П1" : Number(score[1]) < Number(score[2]) ? "П2" : "X",
    totalCorners: corners ? Number(corners[1]) : null
  };
}

const day = String(input.date || new Date().toISOString().slice(0, 10));
fs.mkdirSync(OUTPUT_DIR, { recursive: true });
const file = path.join(OUTPUT_DIR, day + ".jsonl");
const existingText = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
const seen = new Set(existingText.split("\n").filter(Boolean).map(line => {
  try { const row = JSON.parse(line); return String(row.source) + "|" + String(row.eventId); }
  catch { return ""; }
}));
const fresh = [];
for (const row of input.predictions) {
  const actual = parseFinalResult(row.actualResult);
  if (!actual || row.eventId === undefined || row.eventId === null) continue;
  const key = String(row.source) + "|" + String(row.eventId);
  if (seen.has(key)) continue;
  fresh.push({
    schemaVersion: 1,
    archivedAt: new Date().toISOString(),
    matchTime: row.time || null,
    source: row.source || null,
    eventId: row.eventId,
    league: row.league || null,
    home: row.home || null,
    away: row.away || null,
    predictions: {
      outcome: row.outcome || null,
      probabilityOutcome: row.probabilityOutcome || null,
      totalGoals: row.totalGoals || null,
      underGoals: row.underGoals || null,
      corners: row.corners || null,
      yellowCards: row.yellowCards || null
    },
    actualResult: row.actualResult,
    actual
  });
  seen.add(key);
}
if (fresh.length) fs.appendFileSync(file, fresh.map(row => JSON.stringify(row)).join("\n") + "\n");
console.log(JSON.stringify({day, scanned: input.predictions.length, finishedArchived: fresh.length, file}));
