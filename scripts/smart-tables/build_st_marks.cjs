#!/usr/bin/env node
// Сборка data/st_marks_<date>.json — черновика сверки претендентов дня со Smart Tables.
//
// Вход: сырой JSON дня ST (результат fetch-st-day.browser.js из браузерной сессии владельца).
// Логика: направление (over/under) выводится ТОЛЬКО из общих тоталов ST
// («Угловые < 10.5», «ЖК > 3.5»); ИТ/форы направления не дают.
// Черновик ОБЯЗАТЕЛЬНО просматривается перед коммитом (см. docs, раздел 7.19).
//
// Запуск: node scripts/smart-tables/build_st_marks.cjs --date 2026-11-24 --st <raw.json> [--write]

const fs = require('fs');
const path = require('path');

const projectRoot = path.join(__dirname, '..', '..');
const board = require(path.join(projectRoot, 'daily-board.js'));

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === '--write') args.write = true;
    else if (argv[i] === '--date') args.date = argv[++i];
    else if (argv[i] === '--st') args.st = argv[++i];
  }
  return args;
}

// «Угловые < 10.5» / «Угловые > 9.5»; «ЖК < 4.5» / «ЖК > 3.5» — только общие тоталы.
function parseStTitle(title) {
  const t = String(title || '');
  let market = null;
  if (/^Угловые/.test(t)) market = 'Угловые';
  else if (/^ЖК/.test(t)) market = 'ЖК';
  if (!market) return null;
  if (/ИТ[12]|Ф[12]\(/.test(t)) return { market, direction: null }; // индивидуальные и форы — не про общий тотал
  const m = t.match(/([<>])\s*(\d+(?:\.\d+)?)/);
  if (!m) return { market, direction: null };
  return { market, direction: m[1] === '<' ? 'under' : 'over', line: Number(m[2]) };
}

function main() {
  const args = parseArgs(process.argv);
  if (!args.date || !args.st) {
    console.error('Использование: node scripts/smart-tables/build_st_marks.cjs --date YYYY-MM-DD --st <raw.json> [--write]');
    process.exit(1);
  }
  const raw = JSON.parse(fs.readFileSync(args.st, 'utf8'));

  const out = {
    schemaVersion: 1,
    date: args.date,
    generatedAt: new Date().toISOString(),
    source: 'smart-tables.ru (личная подписка владельца, сверка)',
    matches: [],
  };

  let withMarks = 0;
  for (const m of raw.matches || []) {
    const marks = [];
    const totals = [];
    for (const [cat, preds] of Object.entries(m.predictions || {})) {
      for (const p of preds) {
        const parsed = parseStTitle(p.title);
        if (!parsed) continue;
        if (parsed.direction) totals.push({ ...parsed, odd: p.odd });
      }
    }
    for (const market of ['Угловые', 'ЖК']) {
      const dirMarks = totals.filter(t => t.market === market);
      if (!dirMarks.length) continue;
      const unders = dirMarks.filter(t => t.direction === 'under').length;
      const overs = dirMarks.filter(t => t.direction === 'over').length;
      if (unders === overs) continue; // противоречивые сигналы — направления нет
      const direction = unders > overs ? 'under' : 'over';
      const lines = dirMarks.filter(t => t.direction === direction)
        .map(t => `${t.direction === 'under' ? 'ТМ' : 'ТБ'} ${t.line}${t.odd ? ` (@${t.odd})` : ''}`);
      marks.push({ market, direction, note: `ST: ${lines.join(', ')}` });
    }
    if (marks.length) withMarks++;
    out.matches.push({
      stMatchId: m.stMatchId,
      competition: m.competition || null,
      home: m.home,
      away: m.away,
      beginAt: m.beginAt,
      marks,
      ...(marks.length === 0 ? { note: 'ST: общих тоталов по нашим рынкам нет' } : {}),
    });
  }

  console.log(`Матчей от ST: ${out.matches.length}, с марками: ${withMarks}`);
  const target = path.join(projectRoot, 'data', `st_marks_${args.date}.json`);
  if (args.write) {
    fs.writeFileSync(target, JSON.stringify(out, null, 2) + '\n', 'utf8');
    console.log(`Записано: ${target}`);
  } else {
    console.log('Черновик (без --write файл не изменён):');
    console.log(JSON.stringify(out.matches.filter(m => m.marks.length), null, 2));
  }
}

main();
