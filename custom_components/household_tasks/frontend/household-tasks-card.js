class HouseholdTasksCard extends HTMLElement {
  setConfig(config) { this.config = config; this.filter = 'open'; this.render(); }
  set hass(hass) { this._hass = hass; this.render(); }
  getCardSize() { return 5; }
  render() {
    if (!this._hass || !this.config) return;
    if (!this.shadowRoot) this.attachShadow({mode:'open'});
    const tasks = Object.entries(this._hass.states)
      .filter(([id,s]) => id.startsWith('sensor.') && s.attributes.task_entry_id && id.endsWith('_status'))
      .map(([id,s]) => ({id, name:(s.attributes.friendly_name || id).replace(/\s+status$/i, ''), due:s.attributes.due, dueAtOpen:s.attributes.due_at_open, opened:s.attributes.last_opened,
        entry:s.attributes.task_entry_id, state:s.state, category:s.attributes.category, triggerType:s.attributes.trigger_type,
        warnings:Array.isArray(s.attributes.entity_warnings)?s.attributes.entity_warnings:[],
        manualAllowed:s.attributes.manual_completion_allowed === true || (s.attributes.manual_completion_allowed == null && (!s.attributes.close_type || s.attributes.close_type === "manual" || s.attributes.close_type.startsWith("manual_or_")))}));
    const upcoming = tasks.filter(t => t.state === 'closed' && t.due && new Date(t.due) > new Date());
    const visible = this.filter === 'upcoming' ? upcoming : tasks.filter(t=>t.state === 'open');
    const formatDateTime = date => {
      const locale = this._hass.locale || {};
      const language = locale.language || this._hass.language || navigator.language || 'en-GB';
      const configuredOrder = String(locale.date_format || '').toUpperCase();
      const order = ['DMY', 'MDY', 'YMD'].includes(configuredOrder) ? configuredOrder : null;
      const parts = new Intl.DateTimeFormat(language, {year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date);
      const fields = Object.fromEntries(parts.filter(p => ['day','month','year'].includes(p.type)).map(p => [p.type,p.value]));
      const datePart = order === 'DMY' ? [fields.day,fields.month,fields.year].join('/') :
        order === 'MDY' ? [fields.month,fields.day,fields.year].join('/') :
        order === 'YMD' ? [fields.year,fields.month,fields.day].join('-') :
        new Intl.DateTimeFormat(language,{year:'numeric',month:'2-digit',day:'2-digit'}).format(date);
      const hour12 = locale.time_format === '12';
      const hour24 = locale.time_format === '24';
      const timePart = new Intl.DateTimeFormat(language,{
        hour:'numeric',minute:'2-digit',
        ...(hour12 || hour24 ? {hour12:hour12} : {})
      }).format(date);
      return datePart + ', ' + timePart;
    };
    const dueInfo = t => {
      const raw = t.state === 'open' ? (t.dueAtOpen || t.opened) : t.due;
      if (!raw) return '';
      const date = new Date(raw);
      if (Number.isNaN(date.getTime())) return '';
      if (t.state !== 'open') return 'Due: ' + formatDateTime(date);
      const scheduled = Boolean(t.dueAtOpen);
      const label = scheduled ? 'Due: ' : 'Opened: ';
      const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
      const today = new Date();
      const currentDay = new Date(today.getFullYear(), today.getMonth(), today.getDate());
      const days = Math.round((Date.UTC(currentDay.getFullYear(), currentDay.getMonth(), currentDay.getDate()) -
        Date.UTC(start.getFullYear(), start.getMonth(), start.getDate())) / 86400000);
      const overdue = scheduled ? (days > 0 ? ' · ' + days + (days === 1 ? ' day overdue' : ' days overdue') :
        days === 0 ? ' · Due today' : '') : '';
      return label + formatDateTime(date) + overdue;
    };
    const status = t => {
      const raw = t.state === 'open' ? (t.dueAtOpen || t.opened) : t.due;
      const date = raw ? new Date(raw) : null;
      const valid = date && !Number.isNaN(date.getTime());
      const today = new Date();
      const dayNumber = d => Date.UTC(d.getFullYear(),d.getMonth(),d.getDate()) / 86400000;
      const difference = valid ? dayNumber(date) - dayNumber(today) : null;
      if (t.warnings.length) return {key:'warning',label:'Needs attention'};
      if (t.state === 'closed') return {key:'upcoming',label:'Upcoming'};
      if (t.triggerType === 'numeric' || t.triggerType === 'state') return {key:'automatic',label:'Sensor triggered'};
      if (difference !== null && difference < 0 && t.dueAtOpen) return {key:'overdue',label:'Overdue'};
      if (difference === 0 && t.dueAtOpen) return {key:'today',label:'Due today'};
      return {key:'open',label:'Open'};
    };
    const esc = x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    this.shadowRoot.innerHTML = `<ha-card><div class="wrap"><h2>${esc(this.config.title || 'Household Tasks')}</h2>
      <div class="tabs"><button data-tab="open">Open (${tasks.filter(t=>t.state==='open').length})</button>
      <button data-tab="upcoming">Upcoming (${upcoming.length})</button></div>
      ${tasks.some(t=>t.warnings.length)?`<div class="health-alert">⚠ ${tasks.filter(t=>t.warnings.length).length} task(s) have missing or unavailable entities. Check the warnings below or task status sensors.</div>`:""}
      ${visible.length ? visible.map(t=>`<div class="task status-${status(t).key}"><div class="task-body"><div class="task-title"><b>${esc(t.name)}</b><span class="status-pill">${esc(status(t).label)}</span></div><small>${esc(t.category||'')}
      ${dueInfo(t)?' · '+esc(dueInfo(t)):''}</small>${t.warnings.length?`<small class="warning">⚠ ${esc(t.warnings.join('; '))}</small>`:''}</div>
      ${t.state==='open' && t.manualAllowed?`<button data-complete="${esc(t.entry)}">✓ Done</button>`:''}</div>`).join(''):'<p>No tasks in this view.</p>'}
      </div></ha-card><style>.wrap{padding:16px}h2{margin:0 0 16px;font-size:20px}
      .tabs{display:flex;gap:8px;margin-bottom:12px}.tabs button,.task button{border:1px solid var(--divider-color);
      border-radius:8px;padding:9px;background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer}
      .task{--task-accent:var(--primary-color,#2196f3);display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;margin:8px 0;border-left:4px solid var(--task-accent);border-radius:9px;background:var(--secondary-background-color,rgba(127,127,127,.07))}
      .task-body{min-width:0;flex:1}.task-title{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.task-title b{overflow-wrap:anywhere}
      .status-pill{font-size:11px;font-weight:600;border:1px solid var(--task-accent);color:var(--task-accent);border-radius:12px;padding:2px 8px;white-space:nowrap}
      .status-overdue{--task-accent:var(--error-color,#db4437)}.status-today{--task-accent:var(--warning-color,#e5a100)}
      .status-upcoming{--task-accent:var(--success-color,#43a047)}.status-automatic{--task-accent:var(--info-color,#2196f3)}
      .status-warning{--task-accent:var(--warning-color,#e5a100)}
      .tabs button[data-tab="${this.filter}"]{border-color:var(--primary-color)}
      .task button{flex-shrink:0}.health-alert,.warning{color:var(--warning-color,#c77b00)}.health-alert{margin:8px 0;padding:8px;border:1px solid currentColor;border-radius:8px;font-size:13px}small{display:block;color:var(--secondary-text-color);margin-top:4px}</style>`;
    this.shadowRoot.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{this.filter=b.dataset.tab;this.render()});
    this.shadowRoot.querySelectorAll('[data-complete]').forEach(b=>b.onclick=()=>this._hass.callService('household_tasks','complete',{entry_id:b.dataset.complete}));
  }
}
customElements.define('household-tasks-card',HouseholdTasksCard);
window.customCards=window.customCards||[];
window.customCards.push({type:'household-tasks-card',name:'Household Tasks',description:'Open and upcoming household tasks'});
