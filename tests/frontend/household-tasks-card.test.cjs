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
  const range = {}, historyRange = {};
  const root = {
    innerHTML: '',
    querySelectorAll: selector => buttons[selector] || [],
    querySelector: selector => selector === '#upcoming-range' ? range : selector === '#history-range' ? historyRange : null,
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
  return { card, root, buttons, range, historyRange, calls, sandbox };
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
  assert.equal(button.disabled, false);
  assert.ok(!root.innerHTML.includes('data-early="' + encoded + '" disabled'));
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

for (const early of [false,true]) {
  for (const failure of [false,true]) {
    test('completion reconciles replacement markup: early=' + early + ' failure=' + failure, async () => {
      const {card,root,sandbox} = fixture({}, {}, early ? 'closed' : 'open');
      if (early) {card.filter='upcoming'; card.render();}
      let finish, reject, calls=0;
      card._hass.callService = () => {calls++; return new Promise((resolve,r)=>{finish=resolve;reject=r;});};
      const attr = early ? 'data-early' : 'data-complete';
      const button={disabled:false};
      const pending=card.completeTask(button,'task-1',early);
      assert.ok(root.innerHTML.includes(attr+'="task-1" disabled'));
      const oldState=card._hass.states['sensor.test_status'];
      card.hass={...card._hass,states:{'sensor.test_status':{...oldState,attributes:{...oldState.attributes,category:'Changed'}}}};
      assert.ok(root.innerHTML.includes('Changed'));
      assert.ok(root.innerHTML.includes(attr+'="task-1" disabled'));
      await card.completeTask({disabled:false},'task-1',early);
      assert.equal(calls,1);
      if(failure) reject(new Error('permission denied')); else finish();
      await pending;
      assert.equal(card._pending.size,0);
      assert.ok(root.innerHTML.includes(attr+'="task-1"'));
      assert.ok(!root.innerHTML.includes(attr+'="task-1" disabled'));
    });
  }
}

test('independent pending completions do not block or clear one another', async () => {
  const {card,root}=fixture();
  const first=card._hass.states['sensor.test_status'];
  card.hass={...card._hass,states:{'sensor.test_status':first,'sensor.other':{...first,attributes:{...first.attributes,task_entry_id:'task-2'}}}};
  const resolves={};
  card._hass.callService=(_,__,data)=>new Promise(resolve=>{resolves[data.entry_id]=resolve;});
  const a=card.completeTask({disabled:false},'task-1',false);
  const b=card.completeTask({disabled:false},'task-2',false);
  assert.equal(card._pending.size,2);
  resolves['task-1'](); await a;
  assert.ok(!root.innerHTML.includes('data-complete="task-1" disabled'));
  assert.ok(root.innerHTML.includes('data-complete="task-2" disabled'));
  resolves['task-2'](); await b;
  assert.equal(card._pending.size,0);
});

test('malformed history remains visible with no completion control', () => {
  const {root}=fixture({history_invalid:true,manual_completion_allowed:false,entity_warnings:['History malformed']},{},'unknown');
  assert.ok(root.innerHTML.includes('History malformed'));
  assert.ok(!root.innerHTML.includes('data-complete='));
});

test('keyboard focus follows a matching replacement tab', () => {
  const {card,root,buttons}=fixture();
  let focused=0;
  root.activeElement={dataset:{tab:'open'}};
  buttons['[data-tab]'].push({dataset:{tab:'open'},focus:()=>{focused++;}});
  card.setConfig({title:'Changed title'});
  assert.equal(focused,1);
});

test('default range is configurable and temporary selections remain local', () => {
  const {card,root,range} = fixture({}, {upcoming_range:'14'}, 'closed');
  card.filter='upcoming';
  card.render();
  assert.equal(card.upcomingRange,'14');
  assert.ok(root.innerHTML.includes('value="14" selected'));
  range.onchange({target:{value:'7'}});
  card.setConfig({upcoming_range:'14',title:'New title'});
  assert.equal(card.upcomingRange,'7');
  card.setConfig({upcoming_range:'month'});
  assert.equal(card.upcomingRange,'month');
  card.setConfig({upcoming_range:'invalid'});
  assert.equal(card.upcomingRange,'all');
  assert.ok(card.constructor.getConfigForm().schema.some(field=>field.name==='upcoming_range'));
});

test('closed sensor-triggered task warnings remain visible on all tabs', () => {
  const {card,root} = fixture({trigger_type:'numeric', due:null, entity_warnings:[hostile]}, {}, 'closed');
  for (const tab of ['open','upcoming','history']) {
    card.filter=tab; card.render();
    assert.ok(root.innerHTML.includes('1 task needs attention'));
    assert.ok(root.innerHTML.includes('Test task'));
    assert.ok(root.innerHTML.includes(encoded));
    assert.ok(!root.innerHTML.includes('<img'));
  }
});

test('renamed diagnostic aggregate supplies full warnings and clears', () => {
  const {card,root}=fixture({}, {}, 'closed');
  const state={state:'1',attributes:{household_tasks_error_summary:true,errors:[
    {task_name:hostile,warnings:['Missing sensor']}] }};
  card.hass={...card._hass,states:{...card._hass.states,'sensor.renamed_errors':state}};
  assert.ok(root.innerHTML.includes(encoded));
  assert.ok(root.innerHTML.includes('Missing sensor'));
  card.hass={...card._hass,states:{...card._hass.states,'sensor.renamed_errors':{...state,state:'0',attributes:{...state.attributes,errors:[]}}}};
  assert.ok(!root.innerHTML.includes('task needs attention'));
});

test('history is newest first, escaped, read-only and omits malformed events', () => {
  const {card,root}=fixture({history:[
    {action:'opened',at:'2026-01-01T12:00:00Z'},
    {action:'completed_early',at:'2026-01-03T12:00:00Z'},
    {action:hostile,at:'2026-01-02T12:00:00Z'},
    {action:'broken',at:'invalid'},null]}, {history_range:'all'}, 'closed');
  card.filter='history'; card.render();
  assert.ok(root.innerHTML.indexOf('Completed early') < root.innerHTML.indexOf(encoded));
  assert.ok(root.innerHTML.indexOf(encoded) < root.innerHTML.indexOf('Opened'));
  assert.ok(!root.innerHTML.includes('<img'));
  assert.ok(!root.innerHTML.includes('broken'));
  assert.ok(!root.innerHTML.includes('data-early='));
  assert.ok(!root.innerHTML.includes('data-complete='));
});

test('upcoming updates patch live regions without replacing the select', () => {
  const {card,root,range}=fixture({}, {}, 'closed');
  card.filter='upcoming'; card.render();
  const regions=Object.fromEntries(['h2','.tabs','.alerts','.task-list'].map(key=>[key,{innerHTML:''}]));
  const originalQuery=root.querySelector;
  root.querySelector=selector=>regions[selector] || originalQuery(selector);
  let writes=0;
  let markup=root.innerHTML;
  Object.defineProperty(root,'innerHTML',{get:()=>markup,set:value=>{writes++;markup=value;}});
  root.activeElement=range;
  const first=card._hass.states['sensor.test_status'];
  card.hass={...card._hass,states:{'sensor.test_status':{...first,attributes:{...first.attributes,category:'Changed category'}}}};
  assert.equal(writes,0);
  assert.equal(root.querySelector('#upcoming-range'),range);
  assert.ok(regions['.task-list'].innerHTML.includes('Changed category'));
  range.onchange({target:{value:'14'}});
  assert.equal(card.upcomingRange,'14');
  assert.equal(writes,0);
});

test('history filter defaults, independent selection and retained events', () => {
  const past = days => new Date(Date.now()-days*86400000).toISOString();
  const {card,root,range,historyRange}=fixture({history:[
    {action:'completed',at:past(2),completed_by:'Recent person'},
    {action:'completed',at:past(20),completed_by:'Older person'},
    {action:'completed',at:past(70),completed_by:'Oldest person'}
  ]},{upcoming_range:'14',history_range:'7'},'closed');
  card.filter='history'; card.render();
  assert.ok(root.innerHTML.includes('Recent person'));
  assert.ok(!root.innerHTML.includes('Older person'));
  historyRange.onchange({target:{value:'30'}});
  assert.ok(root.innerHTML.includes('Older person'));
  assert.ok(!root.innerHTML.includes('Oldest person'));
  card.filter='upcoming'; card.render();
  range.onchange({target:{value:'all'}});
  card.filter='history'; card.render();
  assert.equal(card.historyRange,'30');
  historyRange.onchange({target:{value:'all'}});
  assert.ok(root.innerHTML.includes('Oldest person'));
  card.setConfig({history_range:'90'});
  assert.equal(card.historyRange,'90');
  historyRange.onchange({target:{value:'invalid'}});
  assert.equal(card.historyRange,'30');
  assert.ok(card.constructor.getConfigForm().schema.some(field=>field.name==='history_range'));
});

test('history displays escaped user names only for manual completion', () => {
  const at=new Date(Date.now()-3600000).toISOString();
  const {card,root}=fixture({history:[
    {action:'completed',at,completed_by:hostile},
    {action:'completed_early',at,completed_by:'A household member'},
    {action:'completed',at},
    {action:'auto_completed',at,completed_by:'Not a manual actor'}
  ]},{},'closed');
  card.filter='history'; card.render();
  assert.ok(root.innerHTML.includes('Completed by '+encoded));
  assert.ok(root.innerHTML.includes('Completed early by A household member'));
  assert.ok(root.innerHTML.includes('No user recorded'));
  assert.ok(root.innerHTML.includes('Automatically completed'));
  assert.ok(!root.innerHTML.includes('Not a manual actor'));
  assert.ok(!root.innerHTML.includes('<img'));
});

test('history dropdown stays mounted during live updates', () => {
  const at=new Date(Date.now()-3600000).toISOString();
  const {card,root,historyRange}=fixture({history:[]},{},'closed');
  card.filter='history'; card.render();
  const regions=Object.fromEntries(['h2','.tabs','.alerts','.task-list'].map(key=>[key,{innerHTML:''}]));
  const originalQuery=root.querySelector;
  root.querySelector=selector=>regions[selector] || originalQuery(selector);
  let writes=0,markup=root.innerHTML;
  Object.defineProperty(root,'innerHTML',{get:()=>markup,set:value=>{writes++;markup=value;}});
  root.activeElement=historyRange;
  const first=card._hass.states['sensor.test_status'];
  card.hass={...card._hass,states:{'sensor.test_status':{...first,attributes:{...first.attributes,history:[{action:'completed',at,completed_by:'New user'}]}}}};
  assert.equal(writes,0);
  assert.equal(root.querySelector('#history-range'),historyRange);
  assert.ok(regions['.task-list'].innerHTML.includes('New user'));
  historyRange.onchange({target:{value:'7'}});
  assert.equal(card.historyRange,'7');
  assert.equal(writes,0);
});
