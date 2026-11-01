import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const base = "https://he-sytikova.github.io/betting-model-preview/";
const ledgerDir = "data/prediction-ledger";
fs.mkdirSync(ledgerDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const capturedAt = new Date().toISOString();
const pages = [
  { key: "goals", url: base + "goals-model.html", selector: "#markets-body tr" },
  { key: "corners-cards", url: base + "model-v1.html", selector: "#markets-body tr" }
];
const records = [];
for (const spec of pages) {
  try {
    await page.goto(spec.url, { waitUntil: "networkidle", timeout: 90000 });
    await page.waitForTimeout(1500);
    const rows = await page.locator(spec.selector).evaluateAll(nodes =>
      nodes.map(row => Array.from(row.querySelectorAll("td")).map(td => td.innerText.trim()))
    );
    for (const row of rows) {
      if (!row.length) continue;
      records.push({
        model: spec.key,
        capturedAt,
        page: spec.url,
        cells: row,
        rowText: row.join(" | ")
      });
    }
  } catch (error) {
    console.error("Capture failed:", spec.key, error?.message || error);
    process.exitCode = 1;
  }
}
await browser.close();

// Deduplicate by model + rendered row. Do not overwrite prior snapshots: forecast
// revisions are retained as separate timestamped records for later audit.
const file = path.join(ledgerDir, capturedAt.slice(0, 10) + ".jsonl");
const prior = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
const existing = new Set(prior.split("\n").filter(Boolean).map(line => {
  try { const x = JSON.parse(line); return x.model + "|" + x.rowText; } catch { return ""; }
}));
const fresh = records.filter(x => !existing.has(x.model + "|" + x.rowText));
if (fresh.length) fs.appendFileSync(file, fresh.map(x => JSON.stringify(x)).join("\n") + "\n");
console.log(JSON.stringify({ capturedAt, totalRows: records.length, newRows: fresh.length, ledgerFile: file }));
