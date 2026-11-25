{
// Escape text and double-quoted HTML attributes. Never use this for CSS,
// JavaScript or URL contexts; those must use fixed values or safe DOM APIs.
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));
const asText = value => typeof value === 'string' ? value : '';
const validTab = value => ['upcoming', 'history'].includes(value) ? value : 'open';
const validRange = value => ['7', '14', 'month', 'all'].includes(value) ? value : 'all';

class HouseholdTasksCard extends HTMLElement {
  setConfig(config) {
    const defaultRange = validRange(String(config.upcoming_range ?? 'all'));
    if (this._defaultRange !== defaultRange) this.upcomingRange = defaultRange;
    this._defaultRange = defaultRange;
    this.config = config;
    this.filter = validTab(this.filter);
    this.upcomingRange = validRange(this.upcomingRange);
    this.render();
  }
  set hass(hass) { this._hass = hass; this.render(); }
  static getStubConfig() { return { title: 'Household Tasks', upcoming_range: 'all' }; }
  static getConfigForm() {
    return {
      schema: [
        {name:'title', selector:{text:{}}},
        {name:'upcoming_range', selector:{select:{options:[
          {value:'7',label:'7 days'}, {value:'14',label:'14 days'},
          {value:'month',label:'1 month'}, {value:'all',label:'All'}
        ]}}}
      ],
      computeLabel: field => field.name === 'upcoming_range' ? 'Default upcoming time range' : 'Title'
    };
  }
  async completeTask(button, entry, early) {
    this._pending ??= new Set();
    if (this._pending.has(entry) || button.disabled) return;
    this._pending.add(entry);
    button.disabled = true;
    this.render();
    try {
      await this._hass.callService('household_tasks', early ? 'complete_early' : 'complete', {entry_id:entry});
    } catch (error) {
      globalThis.alert('Could not complete task: ' + (error?.message || String(error)));
    } finally {
      this._pending.delete(entry);
      // The original button may have been replaced during the await.
      button.disabled = false;
      this.render();
    }
  }
  getCardSize() { return 5; }
  render() {
    if (!this._hass || !this.config) return;
    if (!this.shadowRoot) this.attachShadow({mode:'open'});
    this.filter = validTab(this.filter);
    this.upcomingRange = validRange(this.upcomingRange);
    const tasks = Object.entries(this._hass.states)
      .filter(([id,s]) => id.startsWith('sensor.') && asText(s?.attributes?.task_entry_id).trim() && (['open','closed'].includes(s.state) || s.attributes.history_invalid === true))
      .map(([id,s]) => ({id, name:asText(s.attributes.task_name) || (asText(s.attributes.friendly_name) || id).replace(/\s+status$/i, ''), due:s.attributes.due, dueAtOpen:s.attributes.due_at_open, opened:s.attributes.last_opened,
        entry:s.attributes.task_entry_id, state:s.state, historyInvalid:s.attributes.history_invalid === true, category:asText(s.attributes.category), triggerType:s.attributes.trigger_type,
        warnings:Array.isArray(s.attributes.entity_warnings)?s.attributes.entity_warnings.filter(warning => typeof warning === 'string'):[],
        history:Array.isArray(s.attributes.history)?s.attributes.history:[],
        completedEarlyToday:s.attributes.completed_early_today === true,
        manualAllowed:s.attributes.manual_completion_allowed === true || (s.attributes.manual_completion_allowed == null && (!s.attributes.close_type || s.attributes.close_type === "manual" || asText(s.attributes.close_type).startsWith("manual_or_")))}));
    // Identify the registered diagnostic sensor by its marker, not its default
    // entity ID, so registry renames do not break the card.
    const aggregate = Object.values(this._hass.states).find(s =>
      s?.attributes?.household_tasks_error_summary === true &&
      !['unknown','unavailable'].includes(s.state) && Array.isArray(s.attributes.errors));
    const errors = aggregate ? aggregate.attributes.errors.filter(item =>
      typeof item?.task_name === 'string' && Array.isArray(item.warnings))
      .map(item=>({name:item.task_name, warnings:item.warnings.filter(w=>typeof w==='string')}))
      .filter(item=>item.warnings.length) :
      tasks.filter(t=>t.warnings.length).map(t=>({name:t.name,warnings:t.warnings}));
    const history = tasks.flatMap(t=>t.history.filter(event=>
      typeof event?.action === 'string' && typeof event.at === 'string' &&
      Number.isFinite(new Date(event.at).getTime()))
      .map(event=>({name:t.name,action:event.action,at:event.at})))
      .sort((a,b)=>new Date(b.at)-new Date(a.at)).slice(0,30);
    const now = new Date();
    const language = this._hass.locale?.language || this._hass.language || navigator.language || 'en-GB';
    let timeZone = this._hass.config?.time_zone;
    try { new Intl.DateTimeFormat(language, {timeZone}).format(now); }
    catch { timeZone = undefined; }
    const dateParts = date => Object.fromEntries(
      new Intl.DateTimeFormat('en-GB', {timeZone, year:'numeric',month:'2-digit',day:'2-digit'})
        .formatToParts(date).filter(p => ['day','month','year'].includes(p.type)).map(p => [p.type,p.value]));
    const dayNumber = date => {
      const p = dateParts(date);
      return Date.UTC(Number(p.year), Number(p.month)-1, Number(p.day)) / 86400000;
    };
    const p = dateParts(now);
    const endOfRange = new Date(Date.UTC(Number(p.year), Number(p.month)-1, Number(p.day)));
    if (this.upcomingRange === 'month') {
      const day = endOfRange.getUTCDate();
      endOfRange.setUTCDate(1);
      endOfRange.setUTCMonth(endOfRange.getUTCMonth()+1);
      const last = new Date(Date.UTC(endOfRange.getUTCFullYear(), endOfRange.getUTCMonth()+1, 0)).getUTCDate();
      endOfRange.setUTCDate(Math.min(day,last));
    } else if (this.upcomingRange !== 'all') {
      endOfRange.setUTCDate(endOfRange.getUTCDate() + Number(this.upcomingRange));
    }
    const upcoming = tasks.filter(t => {
      if (t.state !== 'closed' || !t.due) return false;
      const due = new Date(t.due);
      return !Number.isNaN(due.getTime()) && due > now &&
        (this.upcomingRange === 'all' || dayNumber(due) <= endOfRange.getTime()/86400000);
    }).sort((a,b) => new Date(a.due) - new Date(b.due));
    const visible = this.filter === 'upcoming' ? upcoming : tasks.filter(t=>t.state === 'open' || t.historyInvalid);
    const formatDateTime = date => {
      const locale = this._hass.locale || {};

      const configuredOrder = String(locale.date_format || '').toUpperCase();
      const order = ['DMY', 'MDY', 'YMD'].includes(configuredOrder) ? configuredOrder : null;
      const parts = new Intl.DateTimeFormat(language, {timeZone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date);
      const fields = Object.fromEntries(parts.filter(p => ['day','month','year'].includes(p.type)).map(p => [p.type,p.value]));
      const datePart = order === 'DMY' ? [fields.day,fields.month,fields.year].join('/') :
        order === 'MDY' ? [fields.month,fields.day,fields.year].join('/') :
        order === 'YMD' ? [fields.year,fields.month,fields.day].join('-') :
        new Intl.DateTimeFormat(language,{timeZone,year:'numeric',month:'2-digit',day:'2-digit'}).format(date);
      return datePart;
    };
    const dueInfo = t => {
      const raw = t.state === 'open' ? (t.dueAtOpen || t.opened) : t.due;
      if (!raw) return '';
      const date = new Date(raw);
      if (Number.isNaN(date.getTime())) return '';
      if (t.state !== 'open') return 'Due: ' + formatDateTime(date);
      const scheduled = Boolean(t.dueAtOpen);
      const label = scheduled ? 'Due: ' : 'Opened: ';
      const days = dayNumber(now) - dayNumber(date);
      const overdue = scheduled ? (days > 0 ? ' · ' + days + (days === 1 ? ' day overdue' : ' days overdue') :
        days === 0 ? ' · Due today' : '') : '';
      return label + formatDateTime(date) + overdue;
    };
    const status = t => {
      const raw = t.state === 'open' ? (t.dueAtOpen || t.opened) : t.due;
      const date = raw ? new Date(raw) : null;
      const valid = date && !Number.isNaN(date.getTime());
      const today = new Date();
      const difference = valid ? dayNumber(date) - dayNumber(today) : null;
      if (t.warnings.length) return {key:'warning',label:'Needs attention'};
      if (t.state === 'closed') return {key:'upcoming',label:'Upcoming'};
      if (t.triggerType === 'numeric' || t.triggerType === 'state') return {key:'automatic',label:'Sensor triggered'};
      if (difference !== null && difference < 0 && t.dueAtOpen) return {key:'overdue',label:'Overdue'};
      if (difference === 0 && t.dueAtOpen) return {key:'today',label:'Due today'};
      return {key:'open',label:'Open'};
    };
    const actionLabels = {opened:'Opened', completed:'Completed', completed_early:'Completed early', auto_completed:'Automatically completed', skipped:'Skipped', expired:'Expired', reopened:'Reopened', snoozed:'Snoozed'};
    const historyMarkup = history.length ? history.map(event => {
      const date = new Date(event.at);
      const time = new Intl.DateTimeFormat(language,{timeZone,hour:'2-digit',minute:'2-digit'}).format(date);
      const action = Object.hasOwn(actionLabels,event.action) ? actionLabels[event.action] : event.action.replace(/_/g,' ');
      return `<div class="task history-item"><div class="task-body"><div class="task-title"><b>${escapeHtml(event.name)}</b></div><small>${escapeHtml(action)} · ${escapeHtml(formatDateTime(date))} · ${escapeHtml(time)}</small></div></div>`;
    }).join('') : '<p>No recent task activity.</p>';
    const regions = {
      title: escapeHtml(asText(this.config.title) || 'Household Tasks'),
      tabs: `<button data-tab="open" aria-pressed="${this.filter === 'open' ? 'true' : 'false'}">Open (${tasks.filter(t=>t.state==='open').length})</button>
      <button data-tab="upcoming" aria-pressed="${this.filter === 'upcoming' ? 'true' : 'false'}">Upcoming (${upcoming.length})</button>
      <button data-tab="history" aria-pressed="${this.filter === 'history' ? 'true' : 'false'}">History</button>`,
      alerts: errors.length ? `<div class="health-alert" role="status">⚠ ${errors.length} ${errors.length===1?'task needs':'tasks need'} attention<ul>${errors.map(item=>`<li><b>${escapeHtml(item.name)}</b><br>${escapeHtml(item.warnings.join('; '))}</li>`).join('')}</ul></div>` : '',
      content: this.filter === 'history' ? historyMarkup : (visible.length ? visible.map(t=>`<div class="task status-${status(t).key}"><div class="task-body"><div class="task-title"><b>${escapeHtml(t.name)}</b><span class="status-pill">${escapeHtml(status(t).label)}</span></div><small>${escapeHtml(t.category||'')}
      ${dueInfo(t)?' · '+escapeHtml(dueInfo(t)):''}</small>${t.warnings.length?`<small class="warning">⚠ ${escapeHtml(t.warnings.join('; '))}</small>`:''}</div>
      ${t.manualAllowed && (t.state==='open' || (t.state==='closed' && ['completion','weekly','monthly','yearly'].includes(t.triggerType) && t.due))?`<button data-${t.state==='open'?'complete':'early'}="${escapeHtml(t.entry)}" ${(this._pending?.has(t.entry) || (t.state==='closed' && t.completedEarlyToday)) ? 'disabled aria-disabled="true"' : ''}>✓ ${t.state==='open'?'Done':(t.completedEarlyToday?'Completed today':'Complete early')}</button>`:''}</div>`).join(''):'<p>No tasks in this view.</p>')
    };
    const markup = `<ha-card><div class="wrap"><h2>${regions.title}</h2>
      <div class="tabs">${regions.tabs}</div>
      ${this.filter === 'upcoming' ? `<div class="range-filter"><label for="upcoming-range">Show tasks due within</label>
      <select id="upcoming-range" aria-label="Upcoming task time range">
        <option value="7" ${this.upcomingRange === '7' ? 'selected' : ''}>7 days</option>
        <option value="14" ${this.upcomingRange === '14' ? 'selected' : ''}>14 days</option>
        <option value="month" ${this.upcomingRange === 'month' ? 'selected' : ''}>1 month</option>
        <option value="all" ${this.upcomingRange === 'all' ? 'selected' : ''}>All</option>
      </select></div>` : ''}
      <div class="alerts">${regions.alerts}</div>
      <div class="task-list">${regions.content}</div>
      </div></ha-card><style>.wrap{padding:16px}h2{margin:0 0 16px;font-size:20px}
      .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}.tabs button,.task button{border:1px solid var(--divider-color);
      border-radius:8px;padding:9px;background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer}
      .task{--task-accent:var(--primary-color,#2196f3);display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;margin:8px 0;border-left:4px solid var(--task-accent);border-radius:9px;background:var(--secondary-background-color,rgba(127,127,127,.07))}
      .task-body{min-width:0;flex:1}.task-title{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.task-title b{overflow-wrap:anywhere}
      .status-pill{font-size:11px;font-weight:600;border:1px solid var(--task-accent);color:var(--task-accent);border-radius:12px;padding:2px 8px;white-space:nowrap}
      .status-overdue{--task-accent:var(--error-color,#db4437)}.status-today{--task-accent:var(--warning-color,#e5a100)}
      .status-upcoming{--task-accent:var(--success-color,#43a047)}.status-automatic{--task-accent:var(--info-color,#2196f3)}
      .status-warning{--task-accent:var(--warning-color,#e5a100)}
      .tabs button[aria-pressed="true"]{border-color:var(--primary-color)}
      .range-filter{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 12px}
      .range-filter label{font-size:13px;color:var(--secondary-text-color)}
      .range-filter select{background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:8px;padding:7px 10px;font:inherit}
      .task button{flex-shrink:0;min-height:44px}button:focus-visible,select:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}@media(max-width:420px){.task{flex-wrap:wrap}.task button{width:100%}}.task button:disabled{opacity:.55;cursor:default}.health-alert,.warning{color:var(--warning-color,#c77b00)}.health-alert ul{margin:6px 0 0;padding-inline-start:20px}.health-alert li{margin:4px 0;overflow-wrap:anywhere}.health-alert{margin:8px 0;padding:8px;border:1px solid currentColor;border-radius:8px;font-size:13px}small{display:block;color:var(--secondary-text-color);margin-top:4px}</style>`;
    if (markup === this._lastMarkup) return;
    const focused = this.shadowRoot.activeElement;
    const focusKey = focused?.dataset ? ['tab','complete','early'].find(key => focused.dataset[key] !== undefined) : undefined;
    const focusValue = focusKey ? focused.dataset[focusKey] : undefined;
    const rangeFocused = focused?.id === 'upcoming-range';
    // Keep the native range control mounted when task state changes. Replacing
    // its node closes the OS/browser popup, even if focus is restored.
    const selectors = {title:'h2',tabs:'.tabs',alerts:'.alerts',content:'.task-list'};
    const nodes = Object.fromEntries(Object.entries(selectors).map(([key,selector]) =>
      [key,this.shadowRoot.querySelector(selector)]));
    if (this._lastTab === this.filter && Object.values(nodes).every(Boolean)) {
      for (const key of Object.keys(regions)) {
        if (this._lastRegions?.[key] !== regions[key]) nodes[key].innerHTML = regions[key];
      }
    } else {
      this.shadowRoot.innerHTML = markup;
    }
    this._lastMarkup = markup;
    this._lastTab = this.filter;
    this._lastRegions = regions;
    this.shadowRoot.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{this.filter=validTab(b.dataset.tab);this.render()});
    const rangeSelector = this.shadowRoot.querySelector('#upcoming-range');
    if (rangeSelector) {
      if (rangeSelector.value !== this.upcomingRange) rangeSelector.value = this.upcomingRange;
      rangeSelector.onchange = event => { this.upcomingRange = validRange(event.target.value); this.render(); };
    }
    this.shadowRoot.querySelectorAll('[data-complete]').forEach(b=>b.onclick=()=>this.completeTask(b,b.dataset.complete,false));
    this.shadowRoot.querySelectorAll('[data-early]').forEach(button => {
      button.onclick = async () => {
        if (button.disabled) return;
        const task = tasks.find(t => t.entry === button.dataset.early);
        if (!task) return;
        const confirmed = globalThis.confirm(
          'Complete "' + task.name + '" early?\n\n' +
          'This records a completion now and updates its next due date.'
        );
        if (!confirmed) return;
        await this.completeTask(button,button.dataset.early,true);
      };
    });
    // Match dataset values in JavaScript, never interpolate task IDs into selectors.
    const replacement = rangeFocused ? rangeSelector : focusKey ?
      Array.from(this.shadowRoot.querySelectorAll('[data-' + focusKey + ']'))
        .find(element => element.dataset[focusKey] === focusValue) : null;
    if (replacement && replacement !== focused && !replacement.disabled) replacement.focus?.({preventScroll:true});
  }
}
if (!customElements.get?.('household-tasks-card')) customElements.define('household-tasks-card',HouseholdTasksCard);
window.customCards=window.customCards||[];
if (!window.customCards.some(card => card.type === 'household-tasks-card')) window.customCards.push({type:'household-tasks-card',name:'Household Tasks',description:'Open and upcoming household tasks, errors and recent activity'});
}
