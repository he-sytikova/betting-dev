const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { webcrypto } = require('node:crypto');

const root = path.resolve(__dirname, '..');
const access = require('../access-gate.js');

function memoryStorage() {
  const values = new Map();
  return {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  };
}

test('valid credentials create a tab-scoped access grant', async () => {
  const storage = memoryStorage();
  const expectedHash = await access.credentialHash('owner', 'test-only-password', webcrypto);
  const valid = await access.authenticate('owner', 'test-only-password', {
    crypto: webcrypto,
    storage,
    expectedHash,
  });
  assert.equal(valid, true);
  assert.equal(access.hasAccess(storage), true);
});

test('wrong credentials never create an access grant', async () => {
  const storage = memoryStorage();
  const valid = await access.authenticate('owner', 'wrong-password', { crypto: webcrypto, storage });
  assert.equal(valid, false);
  assert.equal(access.hasAccess(storage), false);
});

test('logout clears only the access grant', () => {
  const storage = memoryStorage();
  storage.setItem(access.STORAGE_KEY, access.ACCESS_VALUE);
  storage.setItem('unrelated', 'kept');
  access.clearAccess(storage);
  assert.equal(access.hasAccess(storage), false);
  assert.equal(storage.getItem('unrelated'), 'kept');
});

test('blocked storage fails closed without throwing', () => {
  const blocked = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
    removeItem() { throw new Error('blocked'); },
  };
  assert.equal(access.hasAccess(blocked), false);
  assert.doesNotThrow(() => access.clearAccess(blocked));
});

test('every HTML page installs the same early access gate', () => {
  const pages = fs.readdirSync(root).filter(name => name.endsWith('.html'));
  assert.ok(pages.length >= 10);
  for (const name of pages) {
    const html = fs.readFileSync(path.join(root, name), 'utf8');
    assert.equal((html.match(/<script src="access-gate\.js"><\/script>/g) || []).length, 1, name);
    assert.equal((html.match(/<link rel="stylesheet" href="access-gate\.css" \/>/g) || []).length, 1, name);
    assert.match(html, /<meta name="viewport"[^>]*>\s*<script>document\.documentElement\.classList\.add\('access-pending'\);<\/script>/, name);
  }
});
