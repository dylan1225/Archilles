const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');
const path = require('node:path');

function setup(job) {
  const elements = new Map();
  const timers = new Map();
  const registry = new Map();
  let nextTimer = 0;
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, { childElementCount: 0, hidden: false });
    return elements.get(selector);
  };
  const context = vm.createContext({
    HTMLElement: class {
      attachShadow() { this.shadowRoot = { querySelector: element }; }
      getAttribute() { return null; }
    },
    customElements: { define: (name, type) => registry.set(name, type) },
    AbortController,
    fetch: async () => ({ ok: true, json: async () => job }),
    setTimeout: (fn) => { const id = ++nextTimer; timers.set(id, fn); return id; },
    clearTimeout: (id) => timers.delete(id),
    clearInterval() {},
  });
  vm.runInContext(readFileSync(path.join(__dirname, '../static/house-analysis.js'), 'utf8'), context);
  const house = new (registry.get('house-analysis'))();
  house.isConnected = true;
  house.house = { id: 'house123' };
  house.busy = true;
  const shown = [];
  const reports = [];
  house.renderActivity = (list, events) => {
    shown.push(...events.slice(list.childElementCount));
    list.childElementCount = events.length;
  };
  house.renderReport = (...args) => reports.push(args);
  house.updateButton = () => {};
  const drain = () => {
    while (timers.size) {
      const [id, callback] = timers.entries().next().value;
      timers.delete(id);
      callback();
    }
  };
  return { house, element, timers, shown, reports, drain };
}

const recorded = Array.from({ length: 3 }, (_, round) =>
  ['lead', 'surveyor', 'skeptic', 'analyst'].map((agent) => ({ agent, text: `${round + 1}: ${agent}` })),
).flat();

test('a failed response plays all recorded handoffs before showing the error', async () => {
  const { house, element, shown, drain } = setup({ status: 'failed', activity: recorded, error: 'Service interrupted.' });
  element('.assessment-error').hidden = true;
  await house.pollJob('job1', house.version);
  assert.equal(shown.length, 1);
  assert.equal(element('.assessment-error').hidden, true);
  assert.equal(house.busy, true);
  drain();
  assert.deepEqual(shown.map((event) => event.text), recorded.map((event) => event.text));
  assert.equal(element('.assessment-error').hidden, false);
  assert.equal(element('.error-message').textContent, 'Service interrupted.');
  assert.equal(house.busy, false);
});

test('switching buildings cancels playback and its pending failure', async () => {
  const { house, element, timers, shown, drain } = setup({ status: 'failed', activity: recorded, error: 'Old error.' });
  element('.assessment-error').hidden = true;
  await house.pollJob('job1', house.version);
  const stale = [...timers.values()][0];
  house.stopPolling();
  stale();
  drain();
  assert.equal(shown.length, 1);
  assert.equal(element('.assessment-error').hidden, true);
  assert.equal(house.pendingOutcome, null);
});

test('successful reviews finish playback before rendering the report', async () => {
  const report = { model_id: 'house123' };
  const { house, reports, shown, drain } = setup({ status: 'completed', report, activity: recorded });
  await house.pollJob('job1', house.version);
  assert.equal(reports.length, 0);
  drain();
  assert.equal(shown.length, recorded.length);
  assert.equal(reports.length, 1);
  assert.equal(reports[0][0], report);
  assert.equal(house.busy, false);
});

test('an empty failed run does not manufacture any agent activity', async () => {
  const { house, element, shown, timers } = setup({ status: 'failed', activity: [], error: 'No quota.' });
  await house.pollJob('job1', house.version);
  assert.equal(shown.length, 0);
  assert.equal(timers.size, 0);
  assert.equal(element('.error-message').textContent, 'No quota.');
});

test('repeated polling does not duplicate activity or start a second playback timer', async () => {
  const { house, shown, timers, drain } = setup({ status: 'failed', activity: recorded, error: 'Interrupted.' });
  await house.pollJob('job1', house.version);
  await house.pollJob('job1', house.version);
  assert.equal(timers.size, 1);
  assert.equal(shown.length, 1);
  drain();
  assert.deepEqual(shown.map((event) => event.text), recorded.map((event) => event.text));
});
