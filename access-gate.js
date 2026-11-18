(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) api.boot(root);
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  const STORAGE_KEY = 'betting-dev.access.v1';
  const ACCESS_VALUE = 'granted';
  const USERNAME = 'owner';
  const CREDENTIAL_HASH = '59f349cd7fd0b624d5b404be04205dce5ffe704e613285d75bca9a3dd01f5b77';

  async function credentialHash(username, password, cryptoApi) {
    const cryptoImpl = cryptoApi || (typeof window !== 'undefined' ? window.crypto : null);
    if (!cryptoImpl || !cryptoImpl.subtle) throw new Error('Web Crypto API is unavailable');
    const bytes = new TextEncoder().encode(`${username}\n${password}`);
    const digest = await cryptoImpl.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  }

  function hasAccess(storage) {
    try {
      return Boolean(storage && storage.getItem(STORAGE_KEY) === ACCESS_VALUE);
    } catch (_) {
      return false;
    }
  }

  async function authenticate(username, password, options) {
    const settings = options || {};
    const hash = await credentialHash(String(username || ''), String(password || ''), settings.crypto);
    const expectedHash = settings.expectedHash || CREDENTIAL_HASH;
    const expectedUsername = settings.expectedUsername || USERNAME;
    const valid = hash === expectedHash && username === expectedUsername;
    if (valid && settings.storage) {
      try {
        settings.storage.setItem(STORAGE_KEY, ACCESS_VALUE);
      } catch (_) {
        // The current page can still be unlocked when browser storage is blocked.
      }
    }
    return valid;
  }

  function clearAccess(storage) {
    try {
      if (storage) storage.removeItem(STORAGE_KEY);
    } catch (_) {
      // Reload still restores the locked state when storage is unavailable.
    }
  }

  function sessionStorageFor(win) {
    try {
      return win.sessionStorage;
    } catch (_) {
      return null;
    }
  }

  function addLogout(win) {
    const document = win.document;
    if (document.getElementById('access-logout')) return;
    const button = document.createElement('button');
    button.id = 'access-logout';
    button.className = 'access-logout';
    button.type = 'button';
    button.textContent = 'Выйти';
    button.setAttribute('aria-label', 'Выйти из Betting Dev');
    button.addEventListener('click', () => {
      clearAccess(sessionStorageFor(win));
      win.location.reload();
    });
    document.body.appendChild(button);
  }

  function unlock(win) {
    const document = win.document;
    document.documentElement.classList.remove('access-pending', 'access-locked');
    const gate = document.getElementById('access-gate');
    if (gate) gate.remove();
    addLogout(win);
  }

  function renderGate(win) {
    const document = win.document;
    if (document.getElementById('access-gate')) return;
    const gate = document.createElement('main');
    gate.id = 'access-gate';
    gate.className = 'access-gate';
    gate.setAttribute('aria-labelledby', 'access-title');
    gate.innerHTML = `
      <section class="access-gate__panel">
        <p class="access-gate__label">Betting Dev · закрытый рабочий контур</p>
        <h1 id="access-title">Вход в проект</h1>
        <p class="access-gate__intro">Введите данные доступа, чтобы открыть модели и подготовленные купоны.</p>
        <form id="access-form" class="access-form" novalidate>
          <label for="access-username">Логин</label>
          <input id="access-username" name="username" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required />
          <label for="access-password">Пароль</label>
          <div class="access-form__password">
            <input id="access-password" name="password" type="password" autocomplete="current-password" required />
            <button id="access-toggle" class="access-form__toggle" type="button" aria-pressed="false">Показать</button>
          </div>
          <p id="access-error" class="access-form__error" role="alert" hidden></p>
          <button id="access-submit" class="access-form__submit" type="submit">Войти</button>
        </form>
      </section>`;
    document.body.appendChild(gate);
    document.documentElement.classList.remove('access-pending');
    document.documentElement.classList.add('access-locked');

    const form = document.getElementById('access-form');
    const username = document.getElementById('access-username');
    const password = document.getElementById('access-password');
    const toggle = document.getElementById('access-toggle');
    const submit = document.getElementById('access-submit');
    const error = document.getElementById('access-error');

    toggle.addEventListener('click', () => {
      const show = password.type === 'password';
      password.type = show ? 'text' : 'password';
      toggle.textContent = show ? 'Скрыть' : 'Показать';
      toggle.setAttribute('aria-pressed', String(show));
      password.focus();
    });

    form.addEventListener('submit', async event => {
      event.preventDefault();
      error.hidden = true;
      if (!username.value.trim() || !password.value) {
        error.textContent = 'Введите логин и пароль.';
        error.hidden = false;
        (username.value.trim() ? password : username).focus();
        return;
      }
      submit.disabled = true;
      submit.textContent = 'Проверка…';
      try {
        const valid = await authenticate(username.value.trim(), password.value, {
          crypto: win.crypto,
          storage: sessionStorageFor(win),
        });
        if (valid) {
          unlock(win);
          return;
        }
        error.textContent = 'Неверный логин или пароль.';
        error.hidden = false;
        password.select();
      } catch (_) {
        error.textContent = 'Проверка недоступна в этом браузере. Откройте проект в актуальной версии браузера.';
        error.hidden = false;
      } finally {
        submit.disabled = false;
        submit.textContent = 'Войти';
      }
    });
    username.focus();
  }

  function boot(win) {
    const document = win.document;
    if (hasAccess(sessionStorageFor(win))) {
      document.documentElement.classList.remove('access-pending', 'access-locked');
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => addLogout(win), { once: true });
      } else {
        addLogout(win);
      }
      return;
    }
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => renderGate(win), { once: true });
    } else {
      renderGate(win);
    }
  }

  return {
    STORAGE_KEY,
    ACCESS_VALUE,
    USERNAME,
    authenticate,
    credentialHash,
    hasAccess,
    clearAccess,
    boot,
  };
});
