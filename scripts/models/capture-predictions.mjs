import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const base = "https://he-sytikova.github.io/betting-model-preview/";
const ledgerDir = "data/prediction-ledger";
fs.mkdirSync(ledgerDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ locale: "ru-RU", timezoneId: "Europe/Moscow" });
const records = [];
const specs = [
  { key: "goals", url: base + "goals-model.html", selector: "#markets-body tr", layout: "goals" },
  { key: "corners-cards", url: base + "model-v1.html", selector: "#markets-body tr", layout: "corners-cards" }
];

function parseMoscowDate(value, dateHint) {
  const text = String(value || "").trim();
  let match = text.match(/^(\d{2})\.(\d{2})\.(\d{4})[ ,]+(\d{2}):(\d{2})$/);
  if (match) {
    const [, d, m, y, hh, mm] = match;
    return Date.UTC(Number(y), Number(m)-1, Number(d), Number(hh)-3, Number(mm)); 
  }
  match = text.match(/^(\d{2})\.(\d{2})[ ,]+(\d{2}):(\d{2})$/);
  if (!match || !dateHint) return NaN;
  const [, d, m, hh, mm] = match;
  const hint = String(dateHint).match(/(\d{4})-(\d{2})-(\d{2})/);
  const year = hint ? Number(hint[1]) : new Date().getFullYear();
  return new Date(Date.UTC(year, Number(m)-1, Number(d), Number(hh)-3, Number(mm))).getTime();
}

try {
  for (const spec of specs) {
    try {
      await page.goto(spec.url, { waitUntil: "networkidle", timeout: 90000 });
      await page.waitForFunction(selector => {
        const body = document.querySelector(selector);
        return body && (body.children.length > 0 || document.querySelector("#markets-empty:not(.hidden)") || document.querySelector("#markets-error:not(.hidden)"));
      }, spec.selector, { timeout: 30000 }).catch(() => {});
      await page.waitForTimeout(800);
      const snapshot = await page.evaluate(({ selector, layout }) => {
        const dateHint = document.querySelector("#markets-date")?.innerText || "";
        const rows = Array.from(document.querySelectorAll(selector)).map(tr =>
          Array.from(tr.querySelectorAll("td")).map(td => td.innerText.trim())
        );
        return { dateHint, rows, error: document.querySelector("#markets-error:not(.hidden)")?.innerText || "" };
      }, spec);
      if (snapshot.error) throw new Error(snapshot.error);
      for (const cells of snapshot.rows) {
        if (cells.length < 5) continue;
        const kickoffText = cells[0];
        const kickoffMs = parseMoscowDate(kickoffText, snapshot.dateHint);
        if (!Number.isFinite(kickoffMs)) {
          console.warn("Skipping row with unparseable kickoff:", spec.key, kickoffText);
          continue;
        }
        const now = Date.now();
        if (kickoffMs <= now) continue;
        let league, home, away, marketCells;
        if (spec.layout === "goals") {
          league = cells[1];
          [home, away] = (cells[2] || "").split(/\s+—\s+/);
          marketCells = cells.slice(3);
        } else {
          league = cells[1]; home = cells[2]; away = cells[3];
          marketCells = cells.slice(4);
        }
        if (!home || !away) continue;
        const capturedAt = new Date().toISOString();
        records.push({
          model: spec.key, capturedAt, kickoffAt: new Date(kickoffMs).toISOString(),
          kickoffText, league, home: home.trim(), away: away.trim(),
          markets: marketCells, cells, page: spec.url
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

const day = new Date().toISOString().slice(0, 10);
const file = path.join(ledgerDir, day + ".jsonl");
const prior = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
const existing = new Set(prior.split("\n").filter(Boolean).map(line => {
  try {
    const x = JSON.parse(line);
    return [x.model, x.kickoffAt, x.league, x.home, x.away, JSON.stringify(x.markets)].join("|");
  } catch { return ""; }
}));
const fresh = records.filter(x =>
  !existing.has([x.model, x.kickoffAt, x.league, x.home, x.away, JSON.stringify(x.markets)].join("|"))
);
if (fresh.length) fs.appendFileSync(file, fresh.map(x => JSON.stringify(x)).join("\n") + "\n");
console.log(JSON.stringify({
  capturedAt: new Date().toISOString(),
  futureRowsSeen: records.length,
  newRows: fresh.length,
  skippedStartedOrUnparseable: "started matches are filtered; unparseable kickoff rows are logged",
  ledgerFile: file
}));
