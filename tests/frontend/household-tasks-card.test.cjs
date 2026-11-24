const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../custom_components/household_tasks/frontend/household-tasks-card.js'), 'utf8');
const hostile = '</style><img src=x onerror="alert(1)"> & \' " <script>alert(2)</script>';
const encoded = '&lt;/style&gt;&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; &#39; &quot; &lt;script&gt;alert(2)&lt;/script&gt;';

function fixture(attributes = {}, config = {}, state = 'open') {
  let Card;
  const calls = [];
  const buttons = { '[data-tab]': [], '[data-complete]': [], '[data-early]': [] };
  const range = {};
  const root = {
    innerHTML: '',
    querySelectorAll: selector => buttons[selector] || [],
    querySelector: selector => selector === '#upcoming-range' ? range : null,
  };
  const sandbox = {
    HTMLElement: class { attachShadow() { this.shadowRoot = root; } },
    customElements: { define: (_, cls) => { Card = cls; } },
    window: {},
    navigator: { language: 'en-GB' },
    confirm: () => true,
    alert: () => {},
  };
  vm.runInNewContext(source, sandbox);
  const card = new Card();
  card.setConfig(config);
  card.hass = {
    states: {
      'sensor.test_status': {
        state,
        attributes: {
          task_entry_id: 'task-1', friendly_name: 'Test task',
          category: 'Household', trigger_type: 'completion',
          manual_completion_allowed: true,
          due: '2099-12-31T00:00:00+00:00',
          ...attributes,
        },
      },
    },
    callService: async (...args) => { calls.push(args); },
  };
  return { card, root, buttons, range, calls, sandbox };
}

test('hostile dashboard title is rendered as escaped text', () => {
  const { root } = fixture({}, { title: hostile });
  assert.ok(root.innerHTML.includes('<h2>' + encoded + '</h2>'));
  assert.ok(!root.innerHTML.includes('<img'));
  assert.ok(!root.innerHTML.includes('<script'));
});

for (const field of ['friendly_name', 'category', 'entity_warnings', 'task_entry_id']) {
  test('hostile ' + field + ' remains escaped in markup', () => {
    const { root } = fixture({ [field]: field === 'entity_warnings' ? [hostile] : hostile });
    assert.ok(root.innerHTML.includes(encoded));
    assert.ok(!root.innerHTML.includes('<img'));
    assert.ok(!root.innerHTML.includes('<script'));
    if (field === 'task_entry_id') {
      assert.ok(root.innerHTML.includes('data-complete="' + encoded + '"'));
    }
  });
}

test('literal HTML entities are not interpreted as markup', () => {
  const { root } = fixture({ friendly_name: '&lt;img&gt;' });
  assert.ok(root.innerHTML.includes('&amp;lt;img&amp;gt;'));
});

test('unknown tab and range values cannot reach markup or CSS', () => {
  const { card, root } = fixture();
  card.filter = hostile;
  card.upcomingRange = hostile;
  card.render();
  assert.equal(card.filter, 'open');
  assert.equal(card.upcomingRange, 'all');
  assert.ok(root.innerHTML.includes('data-tab="open" aria-pressed="true"'));
  assert.ok(!root.innerHTML.includes(hostile));
});

test('tab and range event handlers accept only supported choices', () => {
  const { card, buttons, range } = fixture();
  const button = { dataset: { tab: 'upcoming' } };
  buttons['[data-tab]'].push(button);
  card._lastMarkup = undefined;
  card.render();
  button.onclick();
  assert.equal(card.filter, 'upcoming');
  range.onchange({ target: { value: '14' } });
  assert.equal(card.upcomingRange, '14');
  range.onchange({ target: { value: hostile } });
  assert.equal(card.upcomingRange, 'all');
  button.dataset.tab = hostile;
  button.onclick();
  assert.equal(card.filter, 'open');
});

test('unexpected attribute types do not break dashboard rendering', () => {
  const { root } = fixture({
    friendly_name: { unexpected: true }, category: [], entity_warnings: [null, {}, 'Valid warning'],
    manual_completion_allowed: null, close_type: 123,
  });
  assert.ok(root.innerHTML.includes('sensor.test_status'));
  assert.ok(root.innerHTML.includes('Valid warning'));
  assert.ok(!root.innerHTML.includes('[object Object]'));
});

test('non-text task entry identifiers are not rendered as tasks', () => {
  const { root } = fixture({ task_entry_id: { unexpected: true } });
  assert.ok(root.innerHTML.includes('No tasks in this view.'));
  assert.ok(!root.innerHTML.includes('data-complete='));
});

test('normal completion still sends one exact entry identifier', async () => {
  const { card, buttons, calls } = fixture({ task_entry_id: hostile });
  const button = { dataset: { complete: hostile } };
  buttons['[data-complete]'].push(button);
  card._lastMarkup = undefined;
  card.render();
  await button.onclick();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'household_tasks');
  assert.equal(calls[0][1], 'complete');
  assert.equal(calls[0][2].entry_id, hostile);
  assert.deepEqual(Object.keys(calls[0][2]), ['entry_id']);
});

test('early completion retains confirmation and exact entry identifier', async () => {
  const { card, root, buttons, calls, sandbox } = fixture({ task_entry_id: hostile, friendly_name: hostile }, {}, 'closed');
  const button = { dataset: { early: hostile }, disabled: false };
  buttons['[data-early]'].push(button);
  card.filter = 'upcoming';
  card.render();
  assert.ok(root.innerHTML.includes('data-early="' + encoded + '"'));
  sandbox.confirm = () => false;
  await button.onclick();
  assert.equal(calls.length, 0);
  sandbox.confirm = () => true;
  await button.onclick();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1], 'complete_early');
  assert.equal(calls[0][2].entry_id, hostile);
  assert.equal(button.disabled, true);
  await button.onclick();
  assert.equal(calls.length, 1);
});

test('renamed task entities remain discoverable', () => {
  const {card, root} = fixture();
  const state = card._hass.states['sensor.test_status'];
  card.hass = {...card._hass, states: {'sensor.renamed':state}};
  assert.ok(root.innerHTML.includes('Test task'));
  assert.ok(root.innerHTML.includes('data-complete="task-1"'));
});

test('unavailable status sensors are not rendered as open tasks', () => {
  const {root} = fixture({}, {}, 'unavailable');
  assert.ok(root.innerHTML.includes('No tasks in this view.'));
});

test('unrelated state changes do not rebuild identical markup', () => {
  const {card, root} = fixture();
  let writes = 0, markup = root.innerHTML;
  Object.defineProperty(root, 'innerHTML', {
    get: () => markup, set: value => {writes++; markup = value;}
  });
  card.hass = {...card._hass, states:{...card._hass.states,'sensor.unrelated':{state:'1',attributes:{}}}};
  assert.equal(writes, 0);
});

test('explicit task name preserves legitimate status suffix', () => {
  const {root} = fixture({task_name:'Check boiler status'});
  assert.ok(root.innerHTML.includes('Check boiler status'));
});

test('duplicate script registration is harmless', () => {
  const {sandbox} = fixture();
  let registrations = 0;
  sandbox.customElements = {get: () => true, define: () => {registrations++;}};
  vm.runInNewContext(source, sandbox);
  assert.equal(registrations,0);
  assert.equal(sandbox.window.customCards.length,1);
});

test('calendar dates use configured HA timezone rather than browser timezone', () => {
  const {card,root} = fixture({due_at_open:'2026-01-01T01:00:00+00:00'});
  card.hass = {...card._hass, config:{time_zone:'America/Los_Angeles'}, locale:{language:'en-GB',date_format:'DMY'}};
  assert.ok(root.innerHTML.includes('31/12/2025'));
});

test('normal completion blocks concurrent double taps and reports errors', async () => {
  const {card, sandbox} = fixture();
  let reject, calls = 0, alerts = 0;
  sandbox.alert = () => {alerts++;};
  card._hass.callService = () => {calls++; return new Promise((_, r) => {reject = r;});};
  const first = {disabled:false}, second = {disabled:false};
  const pending = card.completeTask(first,'task-1',false);
  await card.completeTask(second,'task-1',false);
  assert.equal(calls,1);
  reject(new Error('failure'));
  await pending;
  assert.equal(first.disabled,false);
  assert.equal(alerts,1);
});
