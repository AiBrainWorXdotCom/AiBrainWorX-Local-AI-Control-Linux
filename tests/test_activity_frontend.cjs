'use strict';
// Exercise the shipped frontend with a small DOM and deterministic fetch/timers.
// No browser, server, model request, process inspection, or package is needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this._text = ''; this._value = '';
    this.checked = false; this.disabled = false; this.hidden = false; this.style = {}; this.dataset = {};
    this.classList = {toggle() {}};
  }
  get textContent() { return this._text + this.children.map(x => x.textContent ?? String(x)).join(''); }
  set textContent(text) { this._text = String(text); this.children = []; }
  get value() { return this._value; }
  set value(value) { this._value = value; }
  set innerHTML(_) { throw Error('Frontend must render data as text, never HTML.'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) {
    this.children = nodes; this._text = '';
    if (this.tagName === 'SELECT') this._value = (nodes.find(x => x.selected) || nodes[0])?.value || '';
  }
  removeAttribute(name) { if (name === 'value') this._value = 0; }
  addEventListener() {}
  querySelector() { return null; }
}

const fixtureState = {activeId: 'fixture', active: {model: {backend: 'strata', modelId: 'fixture-model'}}};
const processSample = {pid: 321, name: 'private-project.exe', cpuPercent: 12.5,
  memoryBytes: 64 * 1048576, gpuMemoryBytes: 16 * 1048576};
const monitor = fingerprint => ({id: 'monitor', name: 'Local monitor', state: 'enabled', fingerprint,
  panel: {metrics: [{label: 'CPU', value: 12.5, unit: '%', state: 'ok'}],
    services: [{name: 'Model', state: 'running', detail: 'Cached status'}],
    processes: [processSample], note: 'Real sampled values'}});
const response = (body, ok = true) => ({ok, async json() { return body; }});
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };

function harness(capture = false) {
  const nodes = new Map(), calls = [], timers = new Map(); let timerId = 0;
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element(id === 'activity-monitor' ? 'select' : 'div')); return nodes.get(id); };
  const document = {hidden: false, getElementById: get,
    querySelector: () => ({content: 'test-token'}), querySelectorAll: () => [],
    createElement: tag => new Element(tag), createTextNode: text => { const n = new Element('#text'); n.textContent = text; return n; }, addEventListener() {}};
  const out = {nodes, calls, timers, document, handler: async route => { throw Error('Unexpected API: ' + route); }};
  const context = vm.createContext({document, location: {search: capture ? '?capture=1' : ''}, URLSearchParams,
    crypto: {randomUUID: () => '12345678-1234-1234-1234-123456789abc'}, console,
    setInterval(fn, ms) { timers.set(++timerId, {fn, ms}); return timerId; }, clearInterval(id) { timers.delete(id); },
    fetch(route, options) {
      // Keep startup refresh suspended; tests seed state without any host access.
      if (route === '/api/state') return new Promise(() => {});
      const call = {route, options, payload: options.body === undefined ? undefined : JSON.parse(options.body)};
      calls.push(call); return out.handler(route, call.payload, options);
    }});
  const script = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
  vm.runInContext(script, context, {filename: 'web/app.js'});
  out.run = source => vm.runInContext(source, context);
  out.context = context; out.get = get;
  out.run('state=' + JSON.stringify(fixtureState) + ';currentPage="activity";');
  out.seed = item => {
    out.run('activityItems=' + JSON.stringify([item]) + ';');
    const option = new Element('option'); option.value = item.id; get('activity-monitor').replaceChildren(option);
  };
  return out;
}

async function run() {
  let checks = 0;
  async function test(name, body) { await body(); checks++; console.log('PASS ' + name); }

  await test('capture hides every process name and preserves real CPU/RAM/GPU values', async () => {
    const ordinary = harness(), privateView = harness(true);
    for (const h of [ordinary, privateView]) {
      h.seed(monitor('a'));
      h.run('activitySamples.set("monitor|a",{rows:' + JSON.stringify([processSample]) + ',at:new Date(0)});renderActivity();');
    }
    const values = h => h.get('activity-processes').children[0].children.map(cell => cell.textContent);
    assert.equal(values(ordinary)[0], 'private-project.exe');
    assert.equal(values(privateView)[0], 'Process 321');
    assert.deepEqual(values(privateView).slice(1), values(ordinary).slice(1));
    assert.equal(values(privateView)[2], privateView.context.panelNumber(12.5));
    assert.equal(values(privateView)[3], privateView.context.panelNumber(processSample.memoryBytes, true));
    assert.equal(values(privateView)[4], privateView.context.panelNumber(processSample.gpuMemoryBytes, true));
    assert.match(privateView.get('activity-process-caption').textContent, /names hidden.*Actual sampled/i);
    assert.doesNotMatch(privateView.get('activity-processes').textContent, /private-project/);
    assert.equal(privateView.get('activity-services').children[0].children[0].className, 'service-state ready');
    assert.equal(privateView.context.panelNumber(null), 'Unavailable');
    assert.equal(privateView.context.panelNumber(NaN), 'Unavailable');
    assert.equal(privateView.context.panelNumber(-1), 'Unavailable');
  });

  await test('hidden Activity pages and disabled monitors never collect', async () => {
    const h = harness();
    h.run('currentPage="plugins";'); await h.context.loadActivity({force: true});
    h.run('currentPage="activity";'); h.document.hidden = true; await h.context.loadActivity({force: true});
    h.document.hidden = false; h.run('activityCanPoll=false;'); await h.context.loadActivity();
    assert.equal(h.calls.length, 0);
  });

  await test('only explicit process refresh requests process and optional GPU data', async () => {
    const h = harness(); h.get('activity-gpu').checked = true;
    h.handler = async route => { assert.equal(route, '/api/plugin-panels'); return response({items: [monitor('a')]}); };
    await h.context.loadActivity({force: true});
    assert.deepEqual(h.calls[0].payload, {inspectProcesses: false, includeGpuProcesses: false});
    await h.context.loadActivity({inspectProcesses: true});
    assert.deepEqual(h.calls[1].payload, {inspectProcesses: true, includeGpuProcesses: true});
    h.get('activity-gpu').checked = false;
    await h.context.loadActivity({inspectProcesses: true});
    assert.deepEqual(h.calls[2].payload, {inspectProcesses: true, includeGpuProcesses: false});
  });

  await test('approval changes discard an in-flight stale panel response', async () => {
    const h = harness(), pending = deferred(); h.handler = () => pending.promise;
    const load = h.context.loadActivity({force: true});
    h.run('currentPage="plugins";resetActivity();');
    pending.resolve(response({items: [monitor('stale')]})); await load;
    assert.equal(h.run('activityItems.length'), 0);
    assert.equal(h.run('activityCanPoll'), false);
    assert.doesNotMatch(h.get('activity-detail').textContent, /Local monitor/);
    assert.equal(h.calls.length, 1);
  });

  await test('light polls preserve a requested snapshot only for the same plugin fingerprint', async () => {
    const h = harness(); let fingerprint = 'a';
    h.handler = async () => response({items: [monitor(fingerprint)]});
    await h.context.loadActivity({inspectProcesses: true});
    assert.match(h.get('activity-processes').textContent, /private-project/);
    await h.context.loadActivity({force: true});
    assert.match(h.get('activity-processes').textContent, /private-project/);
    fingerprint = 'changed'; await h.context.loadActivity({force: true});
    assert.doesNotMatch(h.get('activity-processes').textContent, /private-project/);
    assert.match(h.get('activity-processes').textContent, /No process snapshot/);
  });

  await test('progress displays real X/Y and reaches 100 only for completed jobs', async () => {
    const h = harness(); const run = h.run('updateRun={id:"' + 'a'.repeat(32) + '",active:true,polling:false};updateRun;');
    let progress = {completed: 2, total: 8, state: 'checking', unit: 'checks'};
    h.handler = async route => { assert.equal(route, '/api/update-progress?id=' + run.id); return response(progress); };
    await h.context.pollUpdateProgress(run);
    assert.equal(h.get('update-progress').value, 25);
    assert.equal(h.get('update-progress-label').textContent, '2/8 check jobs completed');
    progress = {...progress, completed: 8}; await h.context.pollUpdateProgress(run);
    assert.equal(h.get('update-progress').value, 99);
    progress = {...progress, state: 'complete'}; await h.context.pollUpdateProgress(run);
    assert.equal(h.get('update-progress').value, 100);
  });

  await test('successful update checks clear only their own progress ID', async () => {
    const h = harness(), baselineTimers = h.timers.size;
    h.handler = async (route, payload) => {
      if (route === '/api/updates') return response({summary: {available: 0, review: 0, unavailable: 0}, components: [], detail: 'Checked'});
      if (route.startsWith('/api/update-progress?id=')) return response({completed: 3, total: 3, state: 'complete', unit: 'checks'});
      assert.equal(route, '/api/update-progress-clear'); return response({note: 'Cleared'});
    };
    await h.context.updates();
    const sent = h.calls.find(x => x.route === '/api/updates').payload.progressId;
    const cleared = h.calls.filter(x => x.route === '/api/update-progress-clear');
    assert.match(sent, /^[a-f0-9]{32}$/); assert.deepEqual(cleared.map(x => x.payload.id), [sent]);
    assert.equal(h.get('update-progress').value, 100);
    assert.equal(h.get('update-progress-label').textContent, '3/3 check jobs completed');
    assert.equal(h.get('check-updates').disabled, false); assert.equal(h.run('updateRun'), null);
    assert.equal(h.timers.size, baselineTimers);
  });

  await test('failed checks retain incomplete real counts and clear the same progress ID', async () => {
    const h = harness(), pending = deferred();
    h.handler = async route => {
      if (route === '/api/updates') return pending.promise;
      if (route.startsWith('/api/update-progress?id=')) return response({completed: 1, total: 4, state: 'checking', unit: 'checks'});
      assert.equal(route, '/api/update-progress-clear'); return response({note: 'Cleared'});
    };
    const operation = h.context.updates(), run = h.run('updateRun');
    await h.context.pollUpdateProgress(run);
    pending.resolve(response({error: 'Fixture source check failed'}, false)); await operation;
    assert.equal(h.get('update-progress').value, 25);
    assert.equal(h.get('update-summary').textContent, 'Check incomplete');
    assert.equal(h.get('update-progress-label').textContent, 'Incomplete · 1/4 check jobs completed');
    assert.deepEqual(h.calls.filter(x => x.route === '/api/update-progress-clear').map(x => x.payload.id), [run.id]);
    assert.equal(h.get('check-updates').disabled, false); assert.equal(h.run('updateRun'), null);
  });

  await test('a failure before the first progress record stays incomplete and cleanup cannot lock the UI', async () => {
    const h = harness();
    h.handler = async route => {
      if (route === '/api/updates') return response({error: 'Fixture check did not start'}, false);
      assert.equal(route, '/api/update-progress-clear'); throw Error('Fixture cleanup unavailable');
    };
    await h.context.updates();
    assert.equal(h.get('update-progress').value, 0);
    assert.equal(h.get('update-progress-label').textContent, 'The check did not finish.');
    assert.equal(h.get('check-updates').disabled, false); assert.equal(h.run('updateRun'), null);
    const own = h.calls.find(x => x.route === '/api/updates').payload.progressId;
    assert.deepEqual(h.calls.filter(x => x.route === '/api/update-progress-clear').map(x => x.payload.id), [own]);
  });

  console.log(`${checks} frontend contracts passed; no browser, host, model or process requests.`);
}
run().catch(error => { console.error(error); process.exitCode = 1; });
