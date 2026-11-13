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
        entry:s.attributes.task_entry_id, state:s.state, category:s.attributes.category,
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
    const esc = x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    this.shadowRoot.innerHTML = `<ha-card><div class="wrap"><h2>${esc(this.config.title || 'Household Tasks')}</h2>
      <div class="tabs"><button data-tab="open">Open (${tasks.filter(t=>t.state==='open').length})</button>
      <button data-tab="upcoming">Upcoming (${upcoming.length})</button></div>
      ${tasks.some(t=>t.warnings.length)?`<div class="health-alert">⚠ ${tasks.filter(t=>t.warnings.length).length} task(s) have missing or unavailable entities. Check the warnings below or task status sensors.</div>`:""}
      ${visible.length ? visible.map(t=>`<div class="task"><div><b>${esc(t.name)}</b><small>${esc(t.category||'')}
      ${dueInfo(t)?' · '+esc(dueInfo(t)):''}</small>${t.warnings.length?`<small class="warning">⚠ ${esc(t.warnings.join('; '))}</small>`:''}</div>
      ${t.state==='open' && t.manualAllowed?`<button data-complete="${esc(t.entry)}">✓ Done</button>`:''}</div>`).join(''):'<p>No tasks in this view.</p>'}
      </div></ha-card><style>.wrap{padding:16px}h2{margin:0 0 16px;font-size:20px}
      .tabs{display:flex;gap:8px;margin-bottom:12px}.tabs button,.task button{border:1px solid var(--divider-color);
      border-radius:8px;padding:9px;background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer}
      .task{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;
      border-bottom:1px solid var(--divider-color)}.health-alert,.warning{color:var(--warning-color,#c77b00)}.health-alert{margin:8px 0;padding:8px;border:1px solid currentColor;border-radius:8px;font-size:13px}small{display:block;color:var(--secondary-text-color);margin-top:4px}</style>`;
    this.shadowRoot.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{this.filter=b.dataset.tab;this.render()});
    this.shadowRoot.querySelectorAll('[data-complete]').forEach(b=>b.onclick=()=>this._hass.callService('household_tasks','complete',{entry_id:b.dataset.complete}));
  }
}
customElements.define('household-tasks-card',HouseholdTasksCard);
window.customCards=window.customCards||[];
window.customCards.push({type:'household-tasks-card',name:'Household Tasks',description:'Open and upcoming household tasks'});
