'use strict';
// Data-hygiene gate for the ai-spend-analysis skill. The shipped fixtures and
// config templates are exactly where a real employee email or an org UUID could
// leak, and .csv files slip past most content scanners. This test closes that
// gap: the shipped data files must contain only synthetic @example.com
// identities, zero airCFO markers and no org UUID. It also asserts, across the
// whole skill tree, that no inline Anthropic key is present.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SKILL_DIR = path.resolve(__dirname, '..', '..');
const DATA_DIRS = [path.join(SKILL_DIR, 'fixtures'), path.join(SKILL_DIR, 'config.example')];

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const ANTHROPIC_KEY_RE = /sk-ant-[A-Za-z0-9_-]{10,}/;
const ALLOWED_EMAIL_DOMAINS = new Set(['example.com']);

function walk(dir) {
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name === 'node_modules') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

test('shipped data files carry only synthetic @example.com identities', () => {
  const files = DATA_DIRS.flatMap(walk);
  assert.ok(files.length > 0, 'expected fixtures and config.example to exist');
  for (const file of files) {
    const rel = path.relative(SKILL_DIR, file);
    const text = fs.readFileSync(file, 'utf8');
    assert.ok(!/aircfo/i.test(text), `${rel} must contain no "aircfo" marker`);
    assert.ok(!UUID_RE.test(text), `${rel} must not embed a real org/account UUID`);
    for (const email of text.match(EMAIL_RE) || []) {
      const domain = email.split('@')[1].toLowerCase();
      assert.ok(ALLOWED_EMAIL_DOMAINS.has(domain),
        `${rel} leaks a non-synthetic email (${email}); data files may use @example.com only`);
    }
  }
});

test('no skill file embeds an inline Anthropic key', () => {
  // __tests__ is excluded: these gate tests legitimately contain the forbidden
  // strings as detection literals (JS source, not shipped data files).
  const skip = new Set(['__pycache__', 'node_modules', '.git', '__tests__']);
  const files = (function all(dir) {
    const out = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (skip.has(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) out.push(...all(full));
      else if (/\.(py|js|json|md|html|csv|txt|yml|yaml)$/.test(entry.name)) out.push(full);
    }
    return out;
  })(SKILL_DIR);
  for (const file of files) {
    const rel = path.relative(SKILL_DIR, file);
    const text = fs.readFileSync(file, 'utf8');
    assert.ok(!ANTHROPIC_KEY_RE.test(text), `${rel} must not contain an inline Anthropic API key`);
  }
});
