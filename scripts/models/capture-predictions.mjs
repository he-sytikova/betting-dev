// Capture the internal model's published predictions into a JSONL ledger.
//
// Source: the live betting-dev pages (goals, corners/cards, fouls/shots/sot).
// Each record stores the raw market cell texts exactly as rendered, so the
// evaluator can parse probabilities without re-running the model.
//
// Layout of all three pages: [kickoff MSK, league, "Home — Away", market cells...].
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const base = "https://he-sytikova.github.io/betting-dev/";
const ledgerDir = "data/prediction-ledger";
fs.mkdirSync(ledgerDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ locale: "ru-RU", timezoneId: "Europe/Moscow" });
const records = [];
const specs = [
  { key: "goals", url: base + "goals-model.html", selector: "#goals-internal-rows tr", empty: "#goals-internal-empty" },
  { key: "corners-cards", url: base + "model-v1.html", selector: "#internal-rows tr", empty: "#internal-empty" },
  { key: "stats", url: base + "stats-model.html", selector: "#stats-internal-rows tr", empty: "#stats-internal-empty" }
];

function parseMoscowDate(value) {
  const text = String(value || "").trim();
  let match = text.match(/^(\d{2})\.(\d{2})\.(\d{4})[ ,]+(\d{2}):(\d{2})$/);
  if (match) {
    const [, d, m, y, hh, mm] = match;
    return Date.UTC(Number(y), Number(m) - 1, Number(d), Number(hh) - 3, Number(mm));
  }
  // Pages render "dd.mm, hh:mm" without a year; infer the nearest future date.
  match = text.match(/^(\d{2})\.(\d{2})[ ,]+(\d{2}):(\d{2})$/);
  if (!match) return NaN;
  const [, d, m, hh, mm] = match;
  const now = new Date();
  for (const year of [now.getUTCFullYear(), now.getUTCFullYear() + 1]) {
    const ms = Date.UTC(year, Number(m) - 1, Number(d), Number(hh) - 3, Number(mm));
    if (ms > now.getTime() - 24 * 3600 * 1000) return ms;
  }
  return NaN;
}

try {
  for (const spec of specs) {
    try {
      await page.goto(spec.url, { waitUntil: "networkidle", timeout: 90000 });
      await page.waitForFunction(({ selector, empty }) => {
        const body = document.querySelector(selector);
        const emptyNote = document.querySelector(empty);
        return (body && body.children.length > 0) || (empty && !empty.hidden);
      }, { selector: spec.selector, empty: spec.empty }, { timeout: 30000 }).catch(() => {});
      await page.waitForTimeout(800);
      const snapshot = await page.evaluate(({ selector }) => {
        const rows = Array.from(document.querySelectorAll(selector)).map(tr =>
          Array.from(tr.querySelectorAll("td")).map(td => td.innerText.trim())
        );
        return { rows };
      }, { selector: spec.selector });
      for (const cells of snapshot.rows) {
        if (cells.length < 4) continue;
        const kickoffText = cells[0];
        const kickoffMs = parseMoscowDate(kickoffText);
        if (!Number.isFinite(kickoffMs)) {
          console.warn("Skipping row with unparseable kickoff:", spec.key, kickoffText);
          continue;
        }
        if (kickoffMs <= Date.now()) continue;
        const league = cells[1];
        const [home, away] = (cells[2] || "").split(/\s+—\s+/);
        if (!home || !away) continue;
        records.push({
          model: spec.key, capturedAt: new Date().toISOString(),
          kickoffAt: new Date(kickoffMs).toISOString(),
          kickoffText, league, home: home.trim(), away: away.trim(),
          markets: cells.slice(3), cells, page: spec.url
        });
      }
    } catch (error) {
      console.error("Capture failed:", spec.key, error?.message || error);
      process.exitCode = 1;
    }
  }
} finally {
  await browser.close();
}

if (records.length) {
  const day = new Date().toISOString().slice(0, 10);
  const file = path.join(ledgerDir, `${day}.jsonl`);
  const seen = new Set();
  if (fs.existsSync(file)) {
    for (const line of fs.readFileSync(file, "utf8").split("\n")) {
      if (!line.trim()) continue;
      try {
        const r = JSON.parse(line);
        seen.add(`${r.model}|${r.kickoffAt}|${r.league}|${r.home}|${r.away}`);
      } catch {}
    }
  }
  const fresh = records.filter(r => !seen.has(`${r.model}|${r.kickoffAt}|${r.league}|${r.home}|${r.away}`));
  if (fresh.length) {
    fs.appendFileSync(file, fresh.map(r => JSON.stringify(r)).join("\n") + "\n");
  }
  console.log(JSON.stringify({ captured: records.length, appended: fresh.length, file }));
} else {
  console.log(JSON.stringify({ captured: 0, appended: 0 }));
}
