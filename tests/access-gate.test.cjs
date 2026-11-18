const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { webcrypto } = require('node:crypto');

const access = require('../access-gate.js');

function storageMock() {
  const values = new Map();
  return {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
    values,
  };
}

test('valid credentials create a tab-scoped access grant', async () => {
  const storage = storageMock();
  const expectedHash = await access.credentialHash('owner', 'test-only-password', webcrypto);
  const valid = await access.authenticate('owner', 'test-only-password', {
    crypto: webcrypto,
    storage,
    expectedHash,
  });
  assert.equal(valid, true);
  assert.equal(access.hasAccess(storage), true);
  assert.equal(storage.getItem(access.STORAGE_KEY), access.ACCESS_VALUE);
});

test('wrong credentials never create an access grant', async () => {
  const storage = storageMock();
  const valid = await access.authenticate('owner', 'definitely-wrong', {
    crypto: webcrypto,
    storage,
  });
  assert.equal(valid, false);
  assert.equal(access.hasAccess(storage), false);
  assert.equal(storage.values.size, 0);
});

test('logout clears only the access grant', () => {
  const storage = storageMock();
  storage.setItem(access.STORAGE_KEY, access.ACCESS_VALUE);
  storage.setItem('unrelated', 'kept');
  access.clearAccess(storage);
  assert.equal(access.hasAccess(storage), false);
  assert.equal(storage.getItem('unrelated'), 'kept');
});

test('blocked storage fails closed without throwing', () => {
  const storage = {
    getItem() { throw new Error('blocked'); },
    removeItem() { throw new Error('blocked'); },
  };
  assert.equal(access.hasAccess(storage), false);
  assert.doesNotThrow(() => access.clearAccess(storage));
});

test('every HTML page installs the same early access gate', () => {
  const root = path.resolve(__dirname, '..');
  const pages = fs.readdirSync(root).filter(name => name.endsWith('.html')).sort();
  assert.ok(pages.includes('index.html'));
  assert.ok(pages.length >= 10);
  for (const page of pages) {
    const html = fs.readFileSync(path.join(root, page), 'utf8');
    assert.equal((html.match(/src="access-gate\.js"/g) || []).length, 1, page);
    assert.equal((html.match(/href="access-gate\.css"/g) || []).length, 1, page);
    assert.ok(html.includes("document.documentElement.classList.add('access-pending')"), page);
    assert.ok(html.indexOf('access-gate.js') < html.indexOf('</head>'), page);
  }
});
