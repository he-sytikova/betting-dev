/* Static football dashboard. GitHub Actions publishes data.json to GitHub Pages. */
const $ = (id) => document.getElementById(id);
let allPredictions = [];
let activeSource = 'summary';
let sortKey = 'time';
let sortAscending = true;
const MIN_DISPLAY_PROBABILITY = 80;

function text(value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}
function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function probabilityParts(value) {
  if (value === null || value === undefined || value === '') return [];
  return String(value).split(/\s*\/\s*/).map((part) => {
    const match = part.match(/(\d+(?:[.,]\d+)?)\s*%/);
    return { part: part.trim(), probability: match ? Number.parseFloat(match[1].replace(',', '.')) : null };
  });
}
function maxProbability(item) {
  const doubleChance = doubleChanceProbabilities(item.probabilityOutcome);
  const fields = [
    item.probabilityOutcome,
    doubleChance.oneX,
    doubleChance.twelve,
    doubleChance.xTwo,
    item.totalGoals,
    item.underGoals,
    item.individualTotals,
    item.corners
  ];
  return fields.flatMap(probabilityParts)
    .reduce((max, entry) => entry.probability === null ? max : Math.max(max, entry.probability), 0);
}
function filterProbabilityText(value) {
  if (value === null || value === undefined || value === '') return value;
  const parts = probabilityParts(value);
  if (!parts.some((part) => part.probability !== null)) return value;
  const kept = parts.filter((part) => part.probability === null || part.probability >= MIN_DISPLAY_PROBABILITY);
  return kept.map((part) => part.part).join(' / ') || '—';
}
function doubleChanceProbabilities(value) {
  const matches = [...String(value || '').matchAll(/(?:П1|X|П2)\s+(\d+(?:[.,]\d+)?)\s*%/g)];
  const values = matches.map((match) => Number.parseFloat(match[1].replace(',', '.')));
  if (values.length !== 3 || values.some((number) => !Number.isFinite(number))) {
    return { oneX: '—', twelve: '—', xTwo: '—' };
  }
  const [home, draw, away] = values;
  return {
    oneX: `${(home + draw).toFixed(1)}%`,
    twelve: `${(home + away).toFixed(1)}%`,
    xTwo: `${(draw + away).toFixed(1)}%`
  };
}
function preparePrediction(item) {
  const peak = maxProbability(item);
  const doubleChance = doubleChanceProbabilities(item.probabilityOutcome);
  // SStats and Euro365 provide full match feeds, not only pre-qualified picks:
  // keep all their events and market probabilities visible.
  const keepAllMarkets = item.source === 'SStats' || item.source === 'Euro365';
  return {
    ...item,
    ...doubleChance,
    maxProbability: peak,
    probabilityOutcome: item.probabilityOutcome || '—',
    totalGoals: keepAllMarkets ? (item.totalGoals || '—') : filterProbabilityText(item.totalGoals),
    underGoals: keepAllMarkets ? (item.underGoals || '—') : filterProbabilityText(item.underGoals),
    individualTotals: keepAllMarkets ? (item.individualTotals || '—') : filterProbabilityText(item.individualTotals),
    corners: keepAllMarkets ? (item.corners || '—') : filterProbabilityText(item.corners)
  };
}
function sortPredictions(rows) {
  return [...rows].sort((a, b) => {
    if (sortKey === 'maxProbability') {
      const result = (a.maxProbability || 0) - (b.maxProbability || 0);
      return sortAscending ? result : -result;
    }
    let left = a[sortKey] ?? '';
    let right = b[sortKey] ?? '';
    if (sortKey === 'time') {
      left = Date.parse(left) || 0;
      right = Date.parse(right) || 0;
    } else {
      left = String(left).toLocaleLowerCase('ru');
      right = String(right).toLocaleLowerCase('ru');
    }
    const result = left < right ? -1 : left > right ? 1 : 0;
    return sortAscending ? result : -result;
  });
}
function addCell(row, value, className) {
  const cell = document.createElement('td');
  cell.textContent = text(value);
  if (className) cell.className = className;
  row.appendChild(cell);
}
function heatmapColor(probability) {
  // 80% is pale green; increasingly strong probabilities move toward dark green.
  const ratio = Math.max(0, Math.min(1, (probability - MIN_DISPLAY_PROBABILITY) / 20));
  const pale = [220, 252, 231];
  const dark = [20, 83, 45];
  const rgb = pale.map((start, index) => Math.round(start + (dark[index] - start) * ratio));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}
function highlightProbabilityCells(row, threshold = MIN_DISPLAY_PROBABILITY) {
  for (const cell of row.cells) {
    // Keep every cell white by default; only qualifying probability cells get heatmap color.
    cell.style.backgroundColor = '#fff';
    cell.style.color = '#000';
    cell.removeAttribute('title');
    const probabilities = probabilityParts(cell.textContent)
      .map((part) => part.probability)
      .filter((value) => value !== null && Number.isFinite(value));
    const highest = probabilities.length ? Math.max(...probabilities) : null;
    if (highest !== null && highest > threshold) {
      cell.style.backgroundColor = heatmapColor(highest);
      cell.title = `Вероятность: ${highest.toFixed(1)}%`;
    }
  }
}
function normalizeTeam(value) {
  return String(value || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('ru')
    .replace(/&/g, ' and ')
    .replace(/\b(fc|afc|cf|sc|sv|fk|sk|club|the)\b/g, ' ')
    .replace(/[^a-zа-яё0-9]/gi, '');
}
function matchKey(item) {
  return `${normalizeTeam(item.home)}|${normalizeTeam(item.away)}`;
}
function bestMarket(item) {
  const candidates = [];
  const addParts = (value) => {
    for (const part of probabilityParts(value)) {
      if (part.probability === null) continue;
      const label = part.part.replace(/\s*:?\s*\d+(?:[.,]\d+)?\s*%.*$/, '').replace(/:$/, '').trim();
      candidates.push({ label: label || 'Вероятность', probability: part.probability });
    }
  };
  addParts(item.probabilityOutcome);
  addParts(item.totalGoals);
  addParts(item.underGoals);
  addParts(item.corners);
  const dc = doubleChanceProbabilities(item.probabilityOutcome);
  for (const [label, value] of [['1X', dc.oneX], ['12', dc.twelve], ['X2', dc.xTwo]]) {
    const parsed = probabilityParts(value)[0];
    if (parsed?.probability !== null && parsed?.probability !== undefined) candidates.push({ label, probability: parsed.probability });
  }
  return candidates.sort((a, b) => b.probability - a.probability)[0] || null;
}
function setSummaryHeaders(isSummary) {
  const headers = [...$('predictions-table').querySelectorAll('thead th')];
  const normalLabels = ['Дата и время', 'Лига', 'Хозяева', 'Гости', 'Вер. исхода (П1/X/П2)', '1X (П1 или X)', '12 (П1 или П2)', 'X2 (X или П2)', 'ТБ (голы)', 'ТМ (голы)', 'Результат матча'];
  const summaryLabels = ['Дата и время', 'Лига', 'Хозяева', 'Гости', 'BSD', 'SStats', 'Euro365', 'FB_DATA', '', ''];
  headers.forEach((header, index) => {
    header.textContent = isSummary ? summaryLabels[index] : normalLabels[index];
    header.style.display = isSummary && index >= 9 ? 'none' : '';
    header.removeAttribute('aria-sort');
  });
}
function renderSummaryTable(tbody) {
  setSummaryHeaders(true);
  const sourceOrder = ['BSD', 'SStats', 'Euro365', 'FB_DATA'];
  const bySource = Object.fromEntries(sourceOrder.map((source) => [source, new Map()]));
  for (const item of allPredictions) {
    if (!bySource[item.source]) continue;
    const key = matchKey(item);
    if (!key || key === '|') continue;
    const best = bestMarket(item);
    const existing = bySource[item.source].get(key);
    if (!existing || (best && (!existing.best || best.probability > existing.best.probability))) {
      bySource[item.source].set(key, { item, best });
    }
  }
  const allKeys = new Set(sourceOrder.flatMap((source) => [...bySource[source].keys()]));
  // A match qualifies only when at least two independent sources offer a best market at or above the high-probability threshold.
  const qualifyingKeys = [...allKeys].filter((key) =>
    sourceOrder.filter((source) => {
      const best = bySource[source].get(key)?.best;
      return best && best.probability >= MIN_DISPLAY_PROBABILITY;
    }).length >= 2
  );
  const rows = qualifyingKeys.map((key) => {
    const entries = Object.fromEntries(sourceOrder.map((source) => [source, bySource[source].get(key) || null]));
    const firstEntry = sourceOrder.map((source) => entries[source]).find(Boolean);
    return { key, entries, item: firstEntry.item };
  });
  rows.sort((a, b) => (Date.parse(a.item.time) || 0) - (Date.parse(b.item.time) || 0));
  if (sortKey === 'maxProbability') rows.sort((a, b) => {
    const secondBest = (match) => sourceOrder
      .map((source) => match.entries[source]?.best?.probability ?? 0)
      .sort((left, right) => right - left)[1] ?? 0;
    const aSecond = secondBest(a);
    const bSecond = secondBest(b);
    return sortAscending ? aSecond - bSecond : bSecond - aSecond;
  });
  $('empty-state').textContent = `Нет матчей, у которых минимум в двух источниках лучший рынок имеет вероятность не ниже ${MIN_DISPLAY_PROBABILITY}%.`;
  $('empty-state').classList.toggle('hidden', rows.length > 0);
  for (const match of rows) {
    const tr = document.createElement('tr');
    addCell(tr, formatTime(match.item.time));
    addCell(tr, match.item.league);
    addCell(tr, match.item.home);
    addCell(tr, match.item.away);
    for (const source of sourceOrder) {
      const best = match.entries[source]?.best;
      addCell(tr, best ? `${best.label}: ${best.probability.toFixed(1)}%` : '—', 'prediction-cell');
    }
    addCell(tr, '');
    addCell(tr, '');
    [...tr.children].forEach((cell, index) => { if (index >= 9) cell.style.display = 'none'; });
    highlightProbabilityCells(tr);
    tbody.appendChild(tr);
  }
}
function renderTable() {
  const tbody = $('table-body');
  tbody.replaceChildren();
  if (activeSource === 'summary') {
    renderSummaryTable(tbody);
    return;
  }
  setSummaryHeaders(false);
  const qualified = allPredictions
    .map(preparePrediction)
    .filter((item) => item.source === 'SStats' ? item.maxProbability > 75 : item.maxProbability >= MIN_DISPLAY_PROBABILITY || item.source === 'Euro365')
    .filter((item) => activeSource === 'all' || item.source === activeSource);
  const rows = sortPredictions(qualified);
  const sourceLabel = activeSource === 'BSD' ? 'BSD' : activeSource === 'SStats' ? 'S-Stats' : activeSource === 'Euro365' ? 'Euro365' : activeSource === 'FB_DATA' ? 'FB_DATA' : '';
  $('empty-state').textContent = `Нет матчей${sourceLabel ? ` из источника ${sourceLabel}` : ''} для отображения.`;
  $('empty-state').classList.toggle('hidden', rows.length > 0);
  for (const item of rows) {
    const tr = document.createElement('tr');
    addCell(tr, formatTime(item.time));
    addCell(tr, item.league);
    addCell(tr, item.home);
    addCell(tr, item.away);
    addCell(tr, item.probabilityOutcome, 'prediction-cell');
    addCell(tr, item.oneX, 'prediction-cell');
    addCell(tr, item.twelve, 'prediction-cell');
    addCell(tr, item.xTwo, 'prediction-cell');
    addCell(tr, item.totalGoals, 'prediction-cell');
    addCell(tr, item.underGoals, 'prediction-cell');
    addCell(tr, item.actualResult, 'result-cell');
    highlightProbabilityCells(tr);
    tbody.appendChild(tr);
  }
}
function setSourceStatus(id, name, status) {
  const el = $(id);
  const count = Number(status?.count || 0);
  el.title = status?.message || status?.diagnostic || '';
  if (status?.ok) {
    el.textContent = `${name}: ${count} матчей`;
    el.className = 'status-badge ok';
  } else {
    el.textContent = `${name}: нет данных`;
    el.className = 'status-badge error';
    el.title = status?.message || status?.diagnostic || 'Подробности отсутствуют в ответе API';
  }
}
async function loadAll(requestRefresh = false) {
  const button = $('refresh-btn');
  const loading = $('loading');
  const errorBox = $('error-container');
  button.disabled = true;
  loading.classList.remove('hidden');
  errorBox.classList.add('hidden');
  $('status-bsd').textContent = 'BSD: загрузка…';
  $('status-sstats').textContent = 'SStats: загрузка…';
  $('status-euro365').textContent = 'Euro365: загрузка…';
  $('status-fbdata').textContent = 'FB_DATA: загрузка…';
  try {
    if (requestRefresh) {
      // GitHub Pages is static: refresh means re-read the latest published snapshot.
    }
    const response = await fetch(`./data.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Не удалось загрузить data.json (HTTP ${response.status})`);
    const data = await response.json();
    const now = Date.now();
    // Hide fixtures that have already started or finished on every main-page tab.
    allPredictions = (Array.isArray(data.predictions) ? data.predictions : [])
      .filter((item) => {
        if (!item.time) return false;
        const kickoff = Date.parse(item.time);
        return Number.isFinite(kickoff) && kickoff > now;
      });
    setSourceStatus('status-bsd', 'BSD', data.sources?.bsd);
    setSourceStatus('status-sstats', 'SStats', data.sources?.sstats);
    setSourceStatus('status-euro365', 'Euro365', data.sources?.euro365);
    setSourceStatus('status-fbdata', 'FB_DATA', data.sources?.fbdata);
    const generatedAt = data.generatedAt ? new Date(data.generatedAt) : null;
    $('current-date').textContent = generatedAt && !Number.isNaN(generatedAt.getTime())
      ? `Матчи на ${data.date || 'сегодня'} · обновлено ${generatedAt.toLocaleString('ru-RU')}`
      : `Матчи на ${data.date || 'сегодня'}`;
    if (data.errors?.length) {
      $('error-message').textContent = data.errors.join(' · ');
      errorBox.classList.remove('hidden');
    }
    renderTable();
  } catch (error) {
    $('error-message').textContent = error.message || 'Не удалось загрузить данные.';
    errorBox.classList.remove('hidden');
    allPredictions = [];
    renderTable();
  } finally {
    loading.classList.add('hidden');
    button.disabled = false;
  }
}
document.addEventListener('DOMContentLoaded', () => {
  $('refresh-btn').addEventListener('click', () => loadAll(true));
  document.querySelectorAll('.source-filter-btn').forEach((button) => {
    button.addEventListener('click', () => {
      activeSource = button.dataset.source || 'all';
      document.querySelectorAll('.source-filter-btn').forEach((item) => {
        const active = item === button;
        item.classList.toggle('active', active);
        item.setAttribute('aria-pressed', String(active));
      });
      renderTable();
    });
  });
  document.querySelectorAll('th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (sortKey === key) sortAscending = !sortAscending;
      else { sortKey = key; sortAscending = true; }
      document.querySelectorAll('th[data-sort]').forEach((item) => {
        item.setAttribute('aria-sort', item === th ? (sortAscending ? 'ascending' : 'descending') : 'none');
      });
      renderTable();
    });
  });
  loadAll();
});
