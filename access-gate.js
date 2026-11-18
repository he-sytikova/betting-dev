(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) {
    root.BettingDevAccess = api;
    if (root.document) api.boot(root);
  }
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  const STORAGE_KEY = 'betting-dev.access.v1';
  const ACCESS_VALUE = 'granted';
  const USERNAME = 'owner';
  // SHA-256 of "username\npassword". The plaintext password is not stored here.
  const CREDENTIAL_HASH = '59f349cd7fd0b624d5b404be04205dce5ffe704e613285d75bca9a3dd01f5b77';

  function hasAccess(storage) {
    try {
      return storage.getItem(STORAGE_KEY) === ACCESS_VALUE;
    } catch (_) {
      return false;
    }
  }

  function clearAccess(storage) {
    try {
      storage.removeItem(STORAGE_KEY);
    } catch (_) {
      // A blocked storage area cannot retain a grant.
    }
  }

  async function credentialHash(username, password, cryptoApi) {
    const cryptoImpl = cryptoApi || (typeof window !== 'undefined' ? window.crypto : null);
    if (!cryptoImpl || !cryptoImpl.subtle) throw new Error('Web Crypto API is unavailable');
    const bytes = new TextEncoder().encode(`${username}\n${password}`);
    const digest = await cryptoImpl.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
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

  function addLogout(win) {
    if (!win.document.body || win.document.querySelector('.access-logout')) return;
    const button = win.document.createElement('button');
    button.type = 'button';
    button.className = 'access-logout';
    button.textContent = 'Выйти';
    button.addEventListener('click', () => {
      clearAccess(win.sessionStorage);
      win.location.reload();
    });
    win.document.body.appendChild(button);
  }

  function unlock(win) {
    const gate = win.document.getElementById('access-gate');
    if (gate) gate.remove();
    win.document.documentElement.classList.remove('access-pending', 'access-locked');
    addLogout(win);
  }

  function renderGate(win) {
    if (win.document.getElementById('access-gate')) return;
    const gate = win.document.createElement('main');
    gate.id = 'access-gate';
    gate.className = 'access-gate';
    gate.innerHTML = `
      <section class="access-card" aria-labelledby="access-title">
        <p class="access-kicker">BETTING DEV · PRIVATE WORKSPACE</p>
        <h1 id="access-title">Вход в проект</h1>
        <p class="access-description">Введите логин и пароль, чтобы открыть модели и прогнозы.</p>
        <form id="access-form" novalidate>
          <label class="access-label" for="access-username">Логин</label>
          <input class="access-input" id="access-username" name="username" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required autofocus />
          <label class="access-label" for="access-password">Пароль</label>
          <div class="access-password-row">
            <input class="access-input" id="access-password" name="password" type="password" autocomplete="current-password" required />
            <button class="access-toggle" id="access-toggle" type="button" aria-pressed="false" aria-label="Показать пароль">Показать</button>
          </div>
          <p class="access-error" id="access-error" role="alert" aria-live="polite"></p>
          <button class="access-submit" type="submit">Войти</button>
        </form>
        <p class="access-note">Доступ сохраняется только в этой вкладке браузера.</p>
      </section>`;
    win.document.body.appendChild(gate);
    win.document.documentElement.classList.remove('access-pending');
    win.document.documentElement.classList.add('access-locked');

    const form = win.document.getElementById('access-form');
    const username = win.document.getElementById('access-username');
    const password = win.document.getElementById('access-password');
    const error = win.document.getElementById('access-error');
    const toggle = win.document.getElementById('access-toggle');
    const submit = form.querySelector('[type="submit"]');

    toggle.addEventListener('click', () => {
      const showing = password.type === 'text';
      password.type = showing ? 'password' : 'text';
      toggle.textContent = showing ? 'Показать' : 'Скрыть';
      toggle.setAttribute('aria-pressed', String(!showing));
      toggle.setAttribute('aria-label', showing ? 'Показать пароль' : 'Скрыть пароль');
      password.focus();
    });

    form.addEventListener('submit', async event => {
      event.preventDefault();
      error.textContent = '';
      if (!username.value.trim() || !password.value) {
        error.textContent = 'Введите логин и пароль.';
        (username.value.trim() ? password : username).focus();
        return;
      }
      submit.disabled = true;
      submit.textContent = 'Проверяем…';
      try {
        const valid = await authenticate(username.value.trim(), password.value, { storage: win.sessionStorage });
        if (valid) {
          unlock(win);
          return;
        }
        error.textContent = 'Неверный логин или пароль.';
        password.select();
      } catch (_) {
        error.textContent = 'Браузер не поддерживает безопасную проверку. Откройте сайт по HTTPS в современном браузере.';
      } finally {
        submit.disabled = false;
        submit.textContent = 'Войти';
      }
    });
  }

  function boot(win) {
    const start = () => {
      if (hasAccess(win.sessionStorage)) unlock(win);
      else renderGate(win);
    };
    if (win.document.body) start();
    else win.document.addEventListener('DOMContentLoaded', start, { once: true });
  }

  return { STORAGE_KEY, ACCESS_VALUE, USERNAME, hasAccess, clearAccess, credentialHash, authenticate, boot };
});
