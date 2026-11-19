(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.CouponConfirmation = api;
}(typeof self !== 'undefined' ? self : this, function (root) {
  'use strict';

  const STORAGE_KEY = 'betting-dev.coupon-confirmations.v1';
  const BET_TYPE_LABELS = Object.freeze({ money: 'Деньги', freebet: 'Фрибет' });
  let mountSequence = 0;
  let sharedStore = null;

  function parseAmount(value) {
    const normalized = String(value ?? '').trim().replace(/\s/g, '').replace(',', '.');
    if (!/^(?:\d+(?:\.\d{1,2})?|\.\d{1,2})$/.test(normalized)) return null;
    const amount = Number(normalized);
    if (!Number.isFinite(amount) || amount <= 0 || !Number.isSafeInteger(Math.round(amount * 100))) return null;
    return amount;
  }

  function couponIdentity(coupon) {
    if (!coupon || !Array.isArray(coupon.legs) || ![1, 2].includes(coupon.legs.length)) return null;
    const legs = coupon.legs.map(leg => ({
      home: String(leg?.home || ''),
      away: String(leg?.away || ''),
      time: String(leg?.time || ''),
      label: String(leg?.label || ''),
      odds: Number(leg?.odds),
    }));
    if (legs.some(leg => !leg.home || !leg.away || !leg.label || !Number.isFinite(leg.odds))) return null;
    return JSON.stringify({
      type: coupon.type,
      bookmaker: String(coupon.bookmaker || ''),
      odds: Number(coupon.odds),
      legs,
    });
  }

  function confirmationKey(day, coupon) {
    const identity = couponIdentity(coupon);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(day || '')) || !identity) return null;
    return JSON.stringify({ day: String(day), coupon: identity });
  }

  function emptyState() {
    return { version: 1, records: {} };
  }

  function readState(storage) {
    if (!storage || typeof storage.getItem !== 'function') throw new Error('storage_unavailable');
    const raw = storage.getItem(STORAGE_KEY);
    if (raw === null) return emptyState();
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.version !== 1 || !parsed.records || typeof parsed.records !== 'object' || Array.isArray(parsed.records)) {
      throw new Error('invalid_storage_schema');
    }
    return parsed;
  }

  function validRecord(record) {
    return record && typeof record === 'object'
      && Number.isFinite(record.amount) && record.amount > 0
      && Object.hasOwn(BET_TYPE_LABELS, record.betType)
      && typeof record.confirmedAt === 'string';
  }

  function createStore(storage, options = {}) {
    const now = typeof options.now === 'function' ? options.now : () => new Date();
    let available = true;
    let state;
    try {
      state = readState(storage);
    } catch (_) {
      available = false;
      state = emptyState();
    }

    function persist(nextState) {
      if (!available) return false;
      try {
        storage.setItem(STORAGE_KEY, JSON.stringify(nextState));
        state = nextState;
        return true;
      } catch (_) {
        return false;
      }
    }

    return {
      isAvailable: () => available,
      get(day, coupon) {
        const key = confirmationKey(day, coupon);
        const record = key ? state.records[key] : null;
        return validRecord(record) ? { ...record } : null;
      },
      confirm(day, coupon, amountValue, betType) {
        if (!available) return { ok: false, reason: 'storage_unavailable' };
        const key = confirmationKey(day, coupon);
        const amount = parseAmount(amountValue);
        if (!key) return { ok: false, reason: 'invalid_coupon' };
        if (amount === null) return { ok: false, reason: 'invalid_amount' };
        if (!Object.hasOwn(BET_TYPE_LABELS, betType)) return { ok: false, reason: 'invalid_bet_type' };
        const record = { amount, betType, confirmedAt: now().toISOString() };
        const nextState = { version: 1, records: { ...state.records, [key]: record } };
        return persist(nextState) ? { ok: true, record: { ...record } } : { ok: false, reason: 'storage_write_failed' };
      },
      remove(day, coupon) {
        if (!available) return { ok: false, reason: 'storage_unavailable' };
        const key = confirmationKey(day, coupon);
        if (!key) return { ok: false, reason: 'invalid_coupon' };
        if (!Object.hasOwn(state.records, key)) return { ok: true };
        const records = { ...state.records };
        delete records[key];
        return persist({ version: 1, records }) ? { ok: true } : { ok: false, reason: 'storage_write_failed' };
      },
    };
  }

  function browserStore() {
    if (sharedStore) return sharedStore;
    let storage = null;
    try {
      storage = root?.localStorage || null;
    } catch (_) {
      storage = null;
    }
    sharedStore = createStore(storage);
    return sharedStore;
  }

  function formatAmount(value) {
    return new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
  }

  function formatConfirmedAt(value) {
    const time = Date.parse(value);
    if (!Number.isFinite(time)) return '';
    return new Date(time).toLocaleString('ru-RU', {
      timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  }

  function mountCoupon(options) {
    const document = root?.document;
    if (!document) throw new Error('document_unavailable');
    const { coupon, day, statusElement } = options;
    const store = options.store || browserStore();
    const container = document.createElement('section');
    const sequence = ++mountSequence;
    container.className = 'stake-confirmation';
    container.setAttribute('aria-label', `Подтверждение ставки: ${coupon.type === 'double' ? 'двойник' : 'одинар'} ${coupon.bookmaker}`);

    function node(tag, className, text) {
      const element = document.createElement(tag);
      if (className) element.className = className;
      if (text !== undefined) element.textContent = text;
      return element;
    }

    function setStatus(record) {
      if (!statusElement) return;
      statusElement.classList.toggle('is-placed', Boolean(record));
      statusElement.textContent = record
        ? `Проставлено · ${BET_TYPE_LABELS[record.betType].toLowerCase()}`
        : 'Подготовлен · не проставлен';
    }

    function render() {
      const record = store.get(day, coupon);
      setStatus(record);
      container.replaceChildren();
      if (!store.isAvailable()) {
        const warning = node('p', 'stake-confirmation__error', 'Локальное хранилище недоступно. Подтверждение отключено, чтобы не потерять отметку незаметно.');
        warning.setAttribute('role', 'alert');
        container.append(warning);
        return;
      }
      if (record) {
        renderConfirmed(record);
      } else {
        renderForm(null);
      }
    }

    function renderConfirmed(record) {
      const summary = node('p', 'stake-confirmation__summary', `${formatAmount(record.amount)} ₽ · ${BET_TYPE_LABELS[record.betType]}`);
      const time = formatConfirmedAt(record.confirmedAt);
      const note = node('p', 'stake-confirmation__saved', time
        ? `Подтверждено вручную: ${time} МСК. Сохранено только в этом браузере.`
        : 'Подтверждено вручную. Сохранено только в этом браузере.');
      const actions = node('div', 'stake-confirmation__actions');
      const edit = node('button', 'stake-confirmation__secondary', 'Изменить');
      edit.type = 'button';
      edit.addEventListener('click', () => renderForm(record));
      const remove = node('button', 'stake-confirmation__remove', 'Снять отметку');
      remove.type = 'button';
      remove.addEventListener('click', () => {
        const result = store.remove(day, coupon);
        if (result.ok) {
          render();
        } else {
          note.textContent = 'Не удалось удалить локальную отметку. Попробуйте ещё раз.';
          note.classList.add('stake-confirmation__error');
          note.setAttribute('role', 'alert');
        }
      });
      actions.append(edit, remove);
      container.append(summary, note, actions);
    }

    function renderForm(existing) {
      container.replaceChildren();
      setStatus(existing);
      const form = node('form', 'stake-form');
      form.noValidate = true;
      const fields = node('div', 'stake-form__fields');

      const amountLabel = node('label', 'stake-form__field');
      amountLabel.htmlFor = `stake-amount-${sequence}`;
      amountLabel.append(node('span', '', 'Сумма, ₽'));
      const amount = node('input', 'stake-form__amount');
      amount.id = `stake-amount-${sequence}`;
      amount.name = 'amount';
      amount.type = 'text';
      amount.inputMode = 'decimal';
      amount.autocomplete = 'off';
      amount.placeholder = '0,00';
      amount.value = existing ? String(existing.amount).replace('.', ',') : '';
      amount.setAttribute('aria-label', `Сумма ставки в рублях: ${coupon.type === 'double' ? 'двойник' : 'одинар'} ${coupon.bookmaker}`);
      amountLabel.append(amount);

      const typeLabel = node('label', 'stake-form__field');
      typeLabel.htmlFor = `stake-type-${sequence}`;
      typeLabel.append(node('span', '', 'Тип ставки'));
      const type = node('select', 'stake-form__type');
      type.id = `stake-type-${sequence}`;
      type.name = 'betType';
      type.setAttribute('aria-label', `Тип ставки: ${coupon.type === 'double' ? 'двойник' : 'одинар'} ${coupon.bookmaker}`);
      for (const [value, label] of Object.entries(BET_TYPE_LABELS)) {
        const option = node('option', '', label);
        option.value = value;
        type.append(option);
      }
      type.value = existing?.betType || 'money';
      typeLabel.append(type);

      const submit = node('button', 'stake-form__submit', existing ? 'Сохранить' : 'Проставлено');
      submit.type = 'submit';
      fields.append(amountLabel, typeLabel, submit);
      form.append(fields);

      const message = node('p', 'stake-form__message');
      message.setAttribute('role', 'status');
      message.textContent = 'Ручная отметка о фактической ставке. Букмекеру ничего не отправляется.';
      form.append(message);

      if (existing) {
        const cancel = node('button', 'stake-confirmation__secondary stake-form__cancel', 'Отмена');
        cancel.type = 'button';
        cancel.addEventListener('click', render);
        fields.append(cancel);
      }

      form.addEventListener('submit', event => {
        event.preventDefault();
        const result = store.confirm(day, coupon, amount.value, type.value);
        if (result.ok) {
          render();
          return;
        }
        message.classList.add('stake-confirmation__error');
        message.setAttribute('role', 'alert');
        if (result.reason === 'invalid_amount') {
          message.textContent = 'Введите сумму больше нуля, не более двух знаков после запятой.';
          amount.setAttribute('aria-invalid', 'true');
          amount.focus();
        } else {
          message.textContent = 'Не удалось сохранить подтверждение. Ставка не отмечена как проставленная.';
        }
      });
      amount.addEventListener('input', () => amount.removeAttribute('aria-invalid'));
      container.append(form);
    }

    render();
    return container;
  }

  return {
    STORAGE_KEY,
    BET_TYPE_LABELS,
    parseAmount,
    couponIdentity,
    confirmationKey,
    createStore,
    mountCoupon,
  };
}));
