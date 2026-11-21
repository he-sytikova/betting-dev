(function (root, factory) {
  'use strict';
  const confirmation = typeof module === 'object' && module.exports
    ? require('./coupon-confirmation.js')
    : root?.CouponConfirmation;
  const api = factory(root, confirmation);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.FinancialReport = api;
}(typeof self !== 'undefined' ? self : this, function (root, confirmation) {
  'use strict';

  const RECONCILIATION_KEY = 'betting-dev.settlement-confirmations.v1';
  const STATUS_LABELS = Object.freeze({ pending: 'Ожидает', win: 'Выигрыш', loss: 'Проигрыш', void: 'Возврат' });
  const PERIOD_LABELS = Object.freeze({ day: 'День', week: 'Неделя', month: 'Месяц' });
  const MONEY_ZERO = Object.freeze({ count: 0, stake: 0, pendingCount: 0, pendingStake: 0, calculatedStake: 0,
    calculatedPayout: 0, calculatedNet: 0, calculatedRoi: null, confirmedCount: 0,
    confirmedStake: 0, confirmedPayout: 0, confirmedNet: 0, confirmedRoi: null });

  function money(value) {
    return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
  }

  function canonicalTime(value) {
    const time = Date.parse(value);
    return Number.isFinite(time) ? new Date(time).toISOString() : null;
  }

  function exactName(value) {
    return String(value || '').normalize('NFKC').trim().replace(/\s+/g, ' ').toLocaleLowerCase('ru');
  }

  function fixtureKey(value) {
    const time = canonicalTime(value?.time);
    const home = exactName(value?.home);
    const away = exactName(value?.away);
    return time && home && away ? JSON.stringify([home, away, time]) : null;
  }

  function buildResultIndex(payloads) {
    const byEvent = new Map();
    const byFixture = new Map();
    const list = Array.isArray(payloads) ? payloads : [payloads];
    for (const payload of list) {
      for (const result of Array.isArray(payload?.results) ? payload.results : []) {
        const eventId = String(result?.event_id || result?.eventId || '').trim();
        const key = fixtureKey(result);
        if (!eventId || !key || !result.home || !result.away) continue;
        byEvent.set(eventId, result);
        const current = byFixture.get(key) || [];
        if (!current.some(row => String(row.event_id || row.eventId) === eventId)) current.push(result);
        byFixture.set(key, current);
      }
    }
    return { byEvent, byFixture };
  }

  function matchResult(leg, index) {
    const eventId = String(leg?.eventId || leg?.event_id || '').trim();
    if (eventId) return index.byEvent.get(eventId) || null;
    const candidates = index.byFixture.get(fixtureKey(leg)) || [];
    return candidates.length === 1 ? candidates[0] : null;
  }

  function settledTotal(label, homeGoals, awayGoals, totalCorners, model) {
    const match = String(label || '').trim().match(/^(ИТ[БМ][12]|Т[БМ])\s+(\d+(?:[.,]\d+)?)$/i);
    if (!match) return { status: 'pending', reason: 'unsupported_market' };
    const kind = match[1].toUpperCase();
    const line = Number(match[2].replace(',', '.'));
    const isCorners = model === 'угловые';
    const isGoals = model === 'голы' || model === 'лига наций';
    if (!isCorners && !isGoals) return { status: 'pending', reason: 'market_provenance_missing' };
    let actual = null;
    if (isCorners && (kind === 'ТБ' || kind === 'ТМ')) actual = totalCorners;
    if (isGoals && (kind === 'ТБ' || kind === 'ТМ')) actual = homeGoals + awayGoals;
    if (isGoals && kind.startsWith('ИТ')) actual = kind.endsWith('1') ? homeGoals : awayGoals;
    if (!Number.isFinite(actual)) return { status: 'pending', reason: 'statistics_missing' };
    if (actual === line) return { status: 'void', reason: 'total_push' };
    const over = kind.includes('Б');
    return { status: (over ? actual > line : actual < line) ? 'win' : 'loss', reason: 'settled' };
  }

  function settleLeg(leg, index) {
    const result = matchResult(leg, index);
    if (!result) return { status: 'pending', reason: 'result_missing', result: null };
    if (['cancelled', 'abandoned'].includes(result.status)) {
      return { status: 'void', reason: result.status, result };
    }
    if (result.status === 'postponed') return { status: 'pending', reason: 'postponed', result };
    if (result.status !== 'finished' || !Number.isInteger(result.home_goals) || !Number.isInteger(result.away_goals)) {
      return { status: 'pending', reason: 'result_incomplete', result };
    }
    const home = result.home_goals;
    const away = result.away_goals;
    const label = String(leg.label || '').trim();
    if (label === 'П1') return { status: home > away ? 'win' : 'loss', reason: 'settled', result };
    if (label === 'П2') return { status: away > home ? 'win' : 'loss', reason: 'settled', result };
    if (label === 'Х' || label === 'X') return { status: home === away ? 'win' : 'loss', reason: 'settled', result };
    if (label === '1X') return { status: home >= away ? 'win' : 'loss', reason: 'settled', result };
    if (label === 'X2') return { status: away >= home ? 'win' : 'loss', reason: 'settled', result };
    if (label === '12') return { status: home !== away ? 'win' : 'loss', reason: 'settled', result };
    return { ...settledTotal(label, home, away, result.total_corners,
      String(leg.model || leg.modelLabel || '').trim().toLocaleLowerCase('ru')), result };
  }

  function payoutFor(record, status, effectiveOdds) {
    if (status === 'pending') return null;
    const amount = Number(record.amount);
    if (record.betType === 'freebet') {
      if (status !== 'win') return 0;
      return Number.isFinite(effectiveOdds) ? money(amount * Math.max(0, effectiveOdds - 1)) : null;
    }
    if (status === 'loss') return 0;
    if (status === 'void') return amount;
    return Number.isFinite(effectiveOdds) ? money(amount * effectiveOdds) : null;
  }

  function settleCoupon(record, index) {
    const legs = (record?.coupon?.legs || []).map(leg => settleLeg(leg, index));
    if (!legs.length) return { status: 'pending', legs, effectiveOdds: null, calculatedPayout: null, calculatedNet: null };
    let status = 'pending';
    if (legs.some(leg => leg.status === 'loss')) status = 'loss';
    else if (legs.some(leg => leg.status === 'pending')) status = 'pending';
    else if (legs.every(leg => leg.status === 'void')) status = 'void';
    else status = 'win';
    let effectiveOdds = null;
    if (status === 'win') {
      const active = (record.coupon.legs || []).filter((_, i) => legs[i].status === 'win');
      if (active.length === record.coupon.legs.length) effectiveOdds = Number(record.coupon.odds);
      else if (active.every(leg => Number.isFinite(leg.odds) && leg.odds > 1)) {
        effectiveOdds = money(active.reduce((product, leg) => product * leg.odds, 1));
      }
    }
    const calculatedPayout = payoutFor(record, status, effectiveOdds);
    const calculatedNet = calculatedPayout === null ? null
      : money(record.betType === 'freebet' ? calculatedPayout : calculatedPayout - record.amount);
    return { status, legs, hasVoid: legs.some(leg => leg.status === 'void'), effectiveOdds, calculatedPayout, calculatedNet };
  }

  function validDay(value) {
    const day = String(value || '');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
    const date = new Date(`${day}T00:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === day ? date : null;
  }

  function dayString(date) {
    return date.toISOString().slice(0, 10);
  }

  function addDays(date, amount) {
    return new Date(date.getTime() + amount * 86400000);
  }

  function periodRange(period, anchor) {
    const date = validDay(anchor);
    if (!date || !Object.hasOwn(PERIOD_LABELS, period)) return null;
    if (period === 'day') return { from: anchor, to: anchor };
    if (period === 'week') {
      const mondayOffset = (date.getUTCDay() + 6) % 7;
      const from = addDays(date, -mondayOffset);
      return { from: dayString(from), to: dayString(addDays(from, 6)) };
    }
    const from = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1));
    const to = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0));
    return { from: dayString(from), to: dayString(to) };
  }

  function parsePayout(value) {
    const normalized = String(value ?? '').trim().replace(/\s/g, '').replace(',', '.');
    if (!/^(?:\d+(?:\.\d{1,2})?|\.\d{1,2})$/.test(normalized)) return null;
    const amount = Number(normalized);
    if (!Number.isFinite(amount) || amount < 0 || !Number.isSafeInteger(Math.round(amount * 100))) return null;
    return amount;
  }

  function reconciliationKey(record) {
    return confirmation?.confirmationKey(record?.day, record?.coupon) || null;
  }

  function copyReconciliation(record) {
    return record && ['win', 'loss', 'void'].includes(record.status)
      && Number.isFinite(record.payout) && record.payout >= 0
      && Number.isFinite(Date.parse(record.confirmedAt)) ? { ...record } : null;
  }

  function createReconciliationStore(storage, options = {}) {
    const now = typeof options.now === 'function' ? options.now : () => new Date();
    let available = true;
    let records = {};
    try {
      if (!storage || typeof storage.getItem !== 'function') throw new Error('storage_unavailable');
      const raw = storage.getItem(RECONCILIATION_KEY);
      if (raw !== null) {
        const parsed = JSON.parse(raw);
        if (parsed?.version !== 1 || !parsed.records || typeof parsed.records !== 'object' || Array.isArray(parsed.records)) {
          throw new Error('invalid_storage_schema');
        }
        for (const [key, value] of Object.entries(parsed.records)) {
          const valid = copyReconciliation(value);
          if (valid) records[key] = valid;
        }
      }
    } catch (_) {
      available = false;
    }
    return {
      isAvailable: () => available,
      all: () => ({ ...records }),
      get(record) {
        const key = reconciliationKey(record);
        return key ? copyReconciliation(records[key]) : null;
      },
      save(record, status, payoutValue) {
        const key = reconciliationKey(record);
        const payout = parsePayout(payoutValue);
        if (!available) return { ok: false, reason: 'storage_unavailable' };
        if (!key) return { ok: false, reason: 'invalid_coupon' };
        if (!['win', 'loss', 'void'].includes(status)) return { ok: false, reason: 'invalid_status' };
        if (payout === null) return { ok: false, reason: 'invalid_payout' };
        const next = { status, payout, confirmedAt: now().toISOString() };
        try {
          storage.setItem(RECONCILIATION_KEY, JSON.stringify({ version: 1, records: { ...records, [key]: next } }));
          records = { ...records, [key]: next };
          return { ok: true, record: { ...next } };
        } catch (_) {
          return { ok: false, reason: 'storage_write_failed' };
        }
      },
    };
  }

  function emptySummary() {
    return { total: 0, counts: { pending: 0, win: 0, loss: 0, void: 0 },
      money: { ...MONEY_ZERO }, freebet: { count: 0, nominal: 0, pendingNominal: 0,
        calculatedNet: 0, confirmedCount: 0, confirmedNet: 0 } };
  }

  function buildReport(records, payloads, reconciliations, range) {
    const index = buildResultIndex(payloads);
    const summary = emptySummary();
    const rows = [];
    for (const record of Array.isArray(records) ? records : []) {
      if (!record?.day || (range && (record.day < range.from || record.day > range.to))) continue;
      const automatic = settleCoupon(record, index);
      const key = reconciliationKey(record);
      const confirmed = copyReconciliation(key ? reconciliations?.[key] : null);
      const row = { record, key, automatic, confirmed };
      rows.push(row);
      summary.total += 1;
      summary.counts[automatic.status] += 1;
      const target = record.betType === 'freebet' ? summary.freebet : summary.money;
      target.count += 1;
      if (record.betType === 'freebet') {
        target.nominal = money(target.nominal + record.amount);
        if (automatic.status === 'pending') target.pendingNominal = money(target.pendingNominal + record.amount);
        if (Number.isFinite(automatic.calculatedNet)) target.calculatedNet = money(target.calculatedNet + automatic.calculatedNet);
        if (confirmed) {
          target.confirmedCount += 1;
          target.confirmedNet = money(target.confirmedNet + confirmed.payout);
        }
      } else {
        target.stake = money(target.stake + record.amount);
        if (automatic.status === 'pending') {
          target.pendingCount += 1;
          target.pendingStake = money(target.pendingStake + record.amount);
        }
        if (Number.isFinite(automatic.calculatedPayout)) {
          target.calculatedStake = money(target.calculatedStake + record.amount);
          target.calculatedPayout = money(target.calculatedPayout + automatic.calculatedPayout);
          target.calculatedNet = money(target.calculatedNet + automatic.calculatedNet);
        }
        if (confirmed) {
          target.confirmedCount += 1;
          target.confirmedStake = money(target.confirmedStake + record.amount);
          target.confirmedPayout = money(target.confirmedPayout + confirmed.payout);
          target.confirmedNet = money(target.confirmedNet + confirmed.payout - record.amount);
        }
      }
    }
    summary.money.calculatedRoi = summary.money.calculatedStake > 0
      ? summary.money.calculatedNet / summary.money.calculatedStake : null;
    summary.money.confirmedRoi = summary.money.confirmedStake > 0
      ? summary.money.confirmedNet / summary.money.confirmedStake : null;
    rows.sort((a, b) => b.record.confirmedAt.localeCompare(a.record.confirmedAt));
    return { rows, summary };
  }

  function moscowToday(now = new Date()) {
    return new Date(now.getTime() + 3 * 3600000).toISOString().slice(0, 10);
  }

  function formatMoney(value) {
    return Number.isFinite(value) ? `${new Intl.NumberFormat('ru-RU', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    }).format(value)} ₽` : '—';
  }

  function formatPercent(value) {
    return Number.isFinite(value) ? `${(value * 100).toFixed(1).replace('.', ',')}%` : '—';
  }

  function formatDay(value) {
    const date = validDay(value);
    return date ? new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' }).format(date) : value;
  }

  function node(tag, className, text) {
    const element = root.document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function metric(label, value, className = '') {
    const item = node('div', `finance-metric ${className}`.trim());
    item.append(node('span', 'finance-metric__label', label), node('strong', 'finance-metric__value', value));
    return item;
  }

  function mount(element, options = {}) {
    if (!element || !root?.document) throw new Error('document_unavailable');
    const fetchImpl = options.fetch || root.fetch.bind(root);
    const placed = options.listPlaced || confirmation.listPlaced;
    let reconciliationStorage = null;
    try { reconciliationStorage = root.localStorage; } catch (_) { reconciliationStorage = null; }
    const reconciliationStore = options.reconciliationStore || createReconciliationStore(reconciliationStorage);
    const periodInput = element.querySelector('#finance-period-date');
    const rangeLabel = element.querySelector('#finance-range');
    const status = element.querySelector('#finance-status');
    const summaryElement = element.querySelector('#finance-summary');
    const rowsElement = element.querySelector('#finance-rows');
    let period = 'day';
    let payloads = [];
    let loadSequence = 0;

    function currentRecords() {
      return placed().filter(record => {
        const range = periodRange(period, periodInput.value);
        return range && record.day >= range.from && record.day <= range.to;
      });
    }

    function render() {
      const range = periodRange(period, periodInput.value);
      const report = buildReport(currentRecords(), payloads, reconciliationStore.all(), range);
      summaryElement.replaceChildren();
      rowsElement.replaceChildren();
      if (!report.rows.length) {
        summaryElement.append(node('p', 'finance-empty', 'В этом периоде нет ставок, подтверждённых кнопкой «Проставлено».'));
        return;
      }
      const counts = report.summary.counts;
      summaryElement.append(node('p', 'finance-counts', `Исходы периода: ${counts.win} выигрыш(ей) · ${counts.loss} проигрыш(ей) · ${counts.void} возврат(ов) · ${counts.pending} ожидает`));
      const moneySummary = report.summary.money;
      const moneySection = node('section', 'finance-ledger');
      moneySection.append(node('h3', 'finance-ledger__title', 'Денежные ставки'));
      const moneyGrid = node('div', 'finance-metrics');
      moneyGrid.append(
        metric('Ставок', String(moneySummary.count)),
        metric('Сумма ставок', formatMoney(moneySummary.stake)),
        metric('Расчётный результат', formatMoney(moneySummary.calculatedNet),
          moneySummary.calculatedNet > 0 ? 'is-positive' : moneySummary.calculatedNet < 0 ? 'is-negative' : ''),
        metric('Расчётный ROI', formatPercent(moneySummary.calculatedRoi)),
        metric('Сверено с БК', `${moneySummary.confirmedCount} · ${formatMoney(moneySummary.confirmedNet)}`),
        metric('Подтверждённый ROI', formatPercent(moneySummary.confirmedRoi)),
        metric('Ожидают результата', `${moneySummary.pendingCount} · ${formatMoney(moneySummary.pendingStake)}`),
      );
      moneySection.append(moneyGrid);
      const freebet = report.summary.freebet;
      const freebetSection = node('section', 'finance-ledger finance-ledger--freebet');
      freebetSection.append(node('h3', 'finance-ledger__title', 'Фрибеты — отдельно от денежного ROI'));
      const freebetGrid = node('div', 'finance-metrics');
      freebetGrid.append(
        metric('Ставок', String(freebet.count)),
        metric('Номинал', formatMoney(freebet.nominal)),
        metric('Расчётный выигрыш', formatMoney(freebet.calculatedNet)),
        metric('Сверено с БК', `${freebet.confirmedCount} · ${formatMoney(freebet.confirmedNet)}`),
      );
      freebetSection.append(freebetGrid);
      summaryElement.append(moneySection, freebetSection);

      for (const row of report.rows) {
        const record = row.record;
        const details = node('details', 'finance-row');
        const summary = node('summary', 'finance-row__summary');
        const title = record.coupon.type === 'double' ? 'Двойник' : 'Одинар';
        const main = node('span', 'finance-row__main');
        main.append(node('strong', '', `${title} · ${record.coupon.bookmaker}`),
          node('span', 'finance-row__meta', `${formatDay(record.day)} · ${formatMoney(record.amount)} · ${record.betType === 'freebet' ? 'Фрибет' : 'Деньги'}`));
        const automaticLabel = row.automatic.status === 'win' && row.automatic.hasVoid ? 'Выигрыш · возврат плеча' : STATUS_LABELS[row.automatic.status];
        const state = node('span', `finance-status finance-status--${row.automatic.status}`, automaticLabel);
        const netClass = row.automatic.calculatedNet > 0 ? 'is-positive' : row.automatic.calculatedNet < 0 ? 'is-negative' : '';
        const net = node('span', `finance-row__net ${netClass}`,
          Number.isFinite(row.automatic.calculatedNet) ? `${row.automatic.calculatedNet > 0 ? '+' : ''}${formatMoney(row.automatic.calculatedNet)}` : '—');
        summary.append(main, state, net);
        details.append(summary);
        const body = node('div', 'finance-row__body');
        for (let i = 0; i < record.coupon.legs.length; i += 1) {
          const leg = record.coupon.legs[i];
          const legResult = row.automatic.legs[i];
          const legRow = node('div', 'finance-leg');
          legRow.append(node('span', 'finance-leg__match', `${leg.home} — ${leg.away}`),
            node('span', 'finance-leg__market', `${leg.label} · ${Number.isFinite(leg.odds) ? leg.odds.toFixed(2) : '—'}`),
            node('span', `finance-status finance-status--${legResult.status}`, STATUS_LABELS[legResult.status]));
          body.append(legRow);
        }
        const calculation = node('p', 'finance-row__note');
        calculation.textContent = row.automatic.status === 'win'
          ? `Расчётный коэффициент: ${Number.isFinite(row.automatic.effectiveOdds) ? row.automatic.effectiveOdds.toFixed(2) : '—'} · выплата ${formatMoney(row.automatic.calculatedPayout)}`
          : `Расчётная выплата: ${formatMoney(row.automatic.calculatedPayout)}`;
        body.append(calculation);
        if (row.confirmed) {
          body.append(node('p', 'finance-row__confirmed', `Сверено с БК: ${STATUS_LABELS[row.confirmed.status].toLocaleLowerCase('ru')} · фактическая выплата ${formatMoney(row.confirmed.payout)}`));
          if (row.automatic.status !== 'pending' && row.automatic.status !== row.confirmed.status) {
            body.append(node('p', 'finance-row__warning', 'Сверенный статус отличается от автоматического расчёта. В подтверждённом итоге используется введённая владельцем выплата.'));
          }
        }
        const reconcile = node('button', 'finance-reconcile', row.confirmed ? 'Изменить сверку' : 'Сверить с БК');
        reconcile.type = 'button';
        const form = node('form', 'finance-reconcile-form');
        form.hidden = true;
        const statusLabel = node('label', '', 'Фактический статус');
        const select = node('select');
        for (const value of ['win', 'loss', 'void']) {
          const option = node('option', '', STATUS_LABELS[value]);
          option.value = value;
          select.append(option);
        }
        select.value = row.confirmed?.status || (row.automatic.status === 'pending' ? 'win' : row.automatic.status);
        statusLabel.append(select);
        const payoutLabel = node('label', '', record.betType === 'freebet' ? 'Зачислено на денежный баланс, ₽' : 'Фактическая выплата, включая возврат ставки, ₽');
        const input = node('input');
        input.type = 'text';
        input.inputMode = 'decimal';
        input.autocomplete = 'off';
        input.required = true;
        input.value = row.confirmed ? String(row.confirmed.payout) : (Number.isFinite(row.automatic.calculatedPayout) ? String(row.automatic.calculatedPayout) : '');
        payoutLabel.append(input);
        const save = node('button', 'finance-save', 'Сохранить сверку');
        save.type = 'submit';
        const message = node('p', 'finance-form-message');
        message.setAttribute('role', 'status');
        form.append(statusLabel, payoutLabel, save, message);
        reconcile.addEventListener('click', () => {
          form.hidden = !form.hidden;
          if (!form.hidden) input.focus();
        });
        form.addEventListener('submit', event => {
          event.preventDefault();
          const result = reconciliationStore.save(record, select.value, input.value);
          if (!result.ok) {
            message.textContent = result.reason === 'invalid_payout'
              ? 'Введите выплату не меньше нуля, не более двух знаков после запятой.'
              : 'Не удалось сохранить сверку в этом браузере.';
            message.setAttribute('role', 'alert');
            return;
          }
          render();
        });
        body.append(reconcile, form);
        details.append(body);
        rowsElement.append(details);
      }
    }

    async function refresh() {
      const sequence = ++loadSequence;
      status.textContent = 'Загрузка опубликованных результатов…';
      const records = currentRecords();
      try {
        const response = await fetchImpl('./data/results/index.json?t=' + Date.now(), { cache: 'no-store' });
        if (!response.ok) throw new Error('results_index_unavailable');
        const index = await response.json();
        const available = new Set(Array.isArray(index.days) ? index.days : []);
        const days = [...new Set(records.map(record => record.day).filter(day => available.has(day)))].sort();
        const loaded = await Promise.all(days.map(async day => {
          const resultResponse = await fetchImpl(`./data/results/${day}.json?t=${Date.now()}`, { cache: 'no-store' });
          if (!resultResponse.ok) throw new Error(`results_${day}_unavailable`);
          return resultResponse.json();
        }));
        if (sequence !== loadSequence) return;
        payloads = loaded;
        status.textContent = 'Расчёт использует только подтверждённые владельцем ставки. Суммы и сверки хранятся только в этом браузере.';
        render();
      } catch (_) {
        if (sequence !== loadSequence) return;
        payloads = [];
        status.textContent = 'Опубликованные результаты временно недоступны. Подтверждённые ставки сохранены; автоматический расчёт будет обновлён позже.';
        render();
      }
    }

    for (const button of element.querySelectorAll('[data-finance-period]')) {
      button.addEventListener('click', () => {
        period = button.dataset.financePeriod;
        for (const other of element.querySelectorAll('[data-finance-period]')) {
          other.setAttribute('aria-pressed', String(other === button));
        }
        const range = periodRange(period, periodInput.value);
        rangeLabel.textContent = `${PERIOD_LABELS[period]}: ${formatDay(range.from)} — ${formatDay(range.to)}`;
        refresh();
      });
    }
    periodInput.addEventListener('change', () => {
      if (!periodRange(period, periodInput.value)) return;
      const range = periodRange(period, periodInput.value);
      rangeLabel.textContent = `${PERIOD_LABELS[period]}: ${formatDay(range.from)} — ${formatDay(range.to)}`;
      refresh();
    });

    periodInput.value = moscowToday();
    const initialRange = periodRange(period, periodInput.value);
    rangeLabel.textContent = `День: ${formatDay(initialRange.from)}`;
    refresh();
    return { refresh, render };
  }

  return { RECONCILIATION_KEY, STATUS_LABELS, PERIOD_LABELS, canonicalTime, fixtureKey,
    buildResultIndex, matchResult, settleLeg, settleCoupon, payoutFor, periodRange,
    parsePayout, reconciliationKey, createReconciliationStore, buildReport, moscowToday,
    formatMoney, formatPercent, mount };
}));
