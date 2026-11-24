{
// Escape text and double-quoted HTML attributes. Never use this for CSS,
// JavaScript or URL contexts; those must use fixed values or safe DOM APIs.
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));
const asText = value => typeof value === 'string' ? value : '';
const validTab = value => value === 'upcoming' ? 'upcoming' : 'open';
const validRange = value => ['7', '14', 'month', 'all'].includes(value) ? value : 'all';

class HouseholdTasksCard extends HTMLElement {
  setConfig(config) { this.config = config; this.filter = validTab(this.filter); this.upcomingRange = validRange(this.upcomingRange); this.render(); }
  set hass(hass) { this._hass = hass; this.render(); }
  static getStubConfig() { return { title: 'Household Tasks' }; }
  async completeTask(button, entry, early) {
    this._pending ??= new Set();
    if (this._pending.has(entry) || button.disabled) return;
    this._pending.add(entry);
    button.disabled = true;
    try {
      await this._hass.callService('household_tasks', early ? 'complete_early' : 'complete', {entry_id:entry});
    } catch (error) {
      globalThis.alert('Could not complete task: ' + (error?.message || String(error)));
      button.disabled = false;
    } finally {
      this._pending.delete(entry);
    }
  }
  getCardSize() { return 5; }
  render() {
    if (!this._hass || !this.config) return;
    if (!this.shadowRoot) this.attachShadow({mode:'open'});
    this.filter = validTab(this.filter);
    this.upcomingRange = validRange(this.upcomingRange);
    const tasks = Object.entries(this._hass.states)
      .filter(([id,s]) => id.startsWith('sensor.') && asText(s?.attributes?.task_entry_id).trim() && ['open','closed'].includes(s.state))
      .map(([id,s]) => ({id, name:asText(s.attributes.task_name) || (asText(s.attributes.friendly_name) || id).replace(/\s+status$/i, ''), due:s.attributes.due, dueAtOpen:s.attributes.due_at_open, opened:s.attributes.last_opened,
        entry:s.attributes.task_entry_id, state:s.state, category:asText(s.attributes.category), triggerType:s.attributes.trigger_type,
        warnings:Array.isArray(s.attributes.entity_warnings)?s.attributes.entity_warnings.filter(warning => typeof warning === 'string'):[],
        completedEarlyToday:s.attributes.completed_early_today === true,
        manualAllowed:s.attributes.manual_completion_allowed === true || (s.attributes.manual_completion_allowed == null && (!s.attributes.close_type || s.attributes.close_type === "manual" || asText(s.attributes.close_type).startsWith("manual_or_")))}));
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
    const visible = this.filter === 'upcoming' ? upcoming : tasks.filter(t=>t.state === 'open');
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
    const markup = `<ha-card><div class="wrap"><h2>${escapeHtml(asText(this.config.title) || 'Household Tasks')}</h2>
      <div class="tabs"><button data-tab="open" aria-pressed="${this.filter === 'open' ? 'true' : 'false'}">Open (${tasks.filter(t=>t.state==='open').length})</button>
      <button data-tab="upcoming" aria-pressed="${this.filter === 'upcoming' ? 'true' : 'false'}">Upcoming (${upcoming.length})</button></div>
      ${this.filter === 'upcoming' ? `<div class="range-filter"><label for="upcoming-range">Show tasks due within</label>
      <select id="upcoming-range" aria-label="Upcoming task time range">
        <option value="7" ${this.upcomingRange === '7' ? 'selected' : ''}>7 days</option>
        <option value="14" ${this.upcomingRange === '14' ? 'selected' : ''}>14 days</option>
        <option value="month" ${this.upcomingRange === 'month' ? 'selected' : ''}>1 month</option>
        <option value="all" ${this.upcomingRange === 'all' ? 'selected' : ''}>All</option>
      </select></div>` : ''}
      ${tasks.some(t=>t.warnings.length)?`<div class="health-alert" role="status">⚠ ${tasks.filter(t=>t.warnings.length).length} task(s) need attention. Check their status sensors for details.</div>`:""}
      ${visible.length ? visible.map(t=>`<div class="task status-${status(t).key}"><div class="task-body"><div class="task-title"><b>${escapeHtml(t.name)}</b><span class="status-pill">${escapeHtml(status(t).label)}</span></div><small>${escapeHtml(t.category||'')}
      ${dueInfo(t)?' · '+escapeHtml(dueInfo(t)):''}</small>${t.warnings.length?`<small class="warning">⚠ ${escapeHtml(t.warnings.join('; '))}</small>`:''}</div>
      ${t.manualAllowed && (t.state==='open' || (t.state==='closed' && ['completion','weekly','monthly','yearly'].includes(t.triggerType) && t.due))?`<button data-${t.state==='open'?'complete':'early'}="${escapeHtml(t.entry)}" ${(this._pending?.has(t.entry) || (t.state==='closed' && t.completedEarlyToday)) ? 'disabled aria-disabled="true"' : ''}>✓ ${t.state==='open'?'Done':(t.completedEarlyToday?'Completed today':'Complete early')}</button>`:''}</div>`).join(''):'<p>No tasks in this view.</p>'}
      </div></ha-card><style>.wrap{padding:16px}h2{margin:0 0 16px;font-size:20px}
      .tabs{display:flex;gap:8px;margin-bottom:12px}.tabs button,.task button{border:1px solid var(--divider-color);
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
      .task button{flex-shrink:0;min-height:44px}button:focus-visible,select:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}@media(max-width:420px){.task{flex-wrap:wrap}.task button{width:100%}}.task button:disabled{opacity:.55;cursor:default}.health-alert,.warning{color:var(--warning-color,#c77b00)}.health-alert{margin:8px 0;padding:8px;border:1px solid currentColor;border-radius:8px;font-size:13px}small{display:block;color:var(--secondary-text-color);margin-top:4px}</style>`;
    if (markup === this._lastMarkup) return;
    this._lastMarkup = markup;
    this.shadowRoot.innerHTML = markup;
    this.shadowRoot.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{this.filter=validTab(b.dataset.tab);this.render()});
    const rangeSelector = this.shadowRoot.querySelector('#upcoming-range');
    if (rangeSelector) rangeSelector.onchange = event => { this.upcomingRange = validRange(event.target.value); this.render(); };
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
  }
}
if (!customElements.get?.('household-tasks-card')) customElements.define('household-tasks-card',HouseholdTasksCard);
window.customCards=window.customCards||[];
if (!window.customCards.some(card => card.type === 'household-tasks-card')) window.customCards.push({type:'household-tasks-card',name:'Household Tasks',description:'Open and upcoming household tasks'});
}
