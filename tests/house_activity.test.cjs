const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');
const path = require('node:path');

function setup(job) {
  const elements = new Map();
  const timers = new Map();
  const delays = new Map();
  const clock = { elapsed: 0 };
  const registry = new Map();
  let nextTimer = 0;
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, {
      childElementCount: 0, hidden: false,
      replaceChildren() { this.childElementCount = 0; },
    });
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
    setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, fn); delays.set(id, delay); return id; },
    clearTimeout: (id) => { timers.delete(id); delays.delete(id); },
    clearInterval() {},
  });
  vm.runInContext(readFileSync(path.join(__dirname, '../static/house-analysis.js'), 'utf8'), context);
  const house = new (registry.get('house-analysis'))();
  house.isConnected = true;
  house.house = { id: 'house123' };
  house.busy = true;
  const shown = [];
  const demoShown = [];
  const reports = [];
  house.renderActivity = (list, events) => {
    for (const event of events.slice(list.childElementCount)) {
      (event.simulated ? demoShown : shown).push(event);
    }
    list.childElementCount = events.length;
  };
  house.renderReport = (...args) => reports.push(args);
  house.updateButton = () => {};
  const tick = () => {
    const [id, callback] = timers.entries().next().value;
    clock.elapsed += delays.get(id);
    timers.delete(id);
    delays.delete(id);
    callback();
  };
  const drain = () => { while (timers.size) tick(); };
  return { house, element, timers, shown, demoShown, reports, clock, tick, drain };
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
  const { house, reports, shown, demoShown, drain } = setup({ status: 'completed', report, activity: recorded });
  await house.pollJob('job1', house.version);
  assert.equal(reports.length, 0);
  drain();
  assert.equal(shown.length, recorded.length);
  assert.equal(reports.length, 1);
  assert.equal(reports[0][0], report);
  assert.equal(house.busy, false);
  assert.equal(demoShown.length, 0);
});

test('a failed run shows three labelled simulation cycles before revealing the actual error', async () => {
  const { house, element, shown, demoShown, timers, clock, tick, drain } = setup({ status: 'failed', activity: [], error: 'No quota.' });
  await house.pollJob('job1', house.version);
  assert.equal(shown.length, 0);
  assert.equal(timers.size, 1);
  assert.equal(element('.assessment-error').hidden, true);
  assert.equal(element('.live-activity').hidden, false);
  assert.match(element('.activity-label').textContent, /Demo preview/);
  while (demoShown.length < 12) {
    tick();
    assert.equal(element('.assessment-error').hidden, true);
  }
  assert.deepEqual(demoShown.map((event) => event.agent), Array(3).fill(['lead', 'surveyor', 'skeptic', 'analyst']).flat());
  assert.deepEqual(demoShown.map((event) => event.cycle), [1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3]);
  assert.equal(house.busy, true);
  drain();
  assert.equal(shown.length, 0);
  assert.equal(element('.assessment-error').hidden, false);
  assert.equal(element('.error-message').textContent, 'The live review is unavailable right now. Please try again later.');
  assert.equal(house.busy, false);
  assert.equal(clock.elapsed, 30000);
  assert.equal(element('.live-activity').childElementCount, 12);
});

test('provider quota details are omitted and demo steps continue in the same feed', async () => {
  const activity = [
    { agent: 'system', text: 'Review started.', t: 0 },
    { agent: 'system', text: 'First model has no quota or credit left.', t: 1 },
    { agent: 'lead', text: 'Assigning room review.', t: 2 },
    { agent: 'system', text: 'Second model has no credit left.', t: 3 },
  ];
  const { house, element, shown, demoShown, drain } = setup({ status: 'failed', activity, error: 'API quota exhausted.' });
  await house.pollJob('job1', house.version);
  drain();
  assert.deepEqual(shown.map((event) => event.text), ['Review started.', 'Assigning room review.']);
  assert.equal(demoShown.length, 12);
  assert.equal(element('.live-activity').childElementCount, 14);
  assert.equal(demoShown[0].t, 2);
  assert.doesNotMatch(element('.error-message').textContent, /quota|credit/i);
});

test('changing buildings during a demonstration cancels the delayed error', async () => {
  const { house, element, demoShown, timers, drain } = setup({ status: 'failed', activity: [], error: 'Old error.' });
  await house.pollJob('job1', house.version);
  const stale = [...timers.values()][0];
  house.stopPolling();
  stale();
  drain();
  assert.equal(demoShown.length, 1);
  assert.equal(element('.assessment-error').hidden, true);
  assert.equal(house.demoRunning, false);
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
