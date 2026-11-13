class HouseholdTasksCard extends HTMLElement {
  setConfig(config) { this.config = config; this.filter = 'open'; this.render(); }
  set hass(hass) { this._hass = hass; this.render(); }
  getCardSize() { return 5; }
  render() {
    if (!this._hass || !this.config) return;
    if (!this.shadowRoot) this.attachShadow({mode:'open'});
    const tasks = Object.entries(this._hass.states)
      .filter(([id,s]) => id.startsWith('sensor.') && s.attributes.task_entry_id && id.endsWith('_status'))
      .map(([id,s]) => ({id, name:(s.attributes.friendly_name || id).replace(/\s+status$/i, ''), due:s.attributes.due,
        entry:s.attributes.task_entry_id, state:s.state, category:s.attributes.category}));
    const upcoming = tasks.filter(t => t.state === 'closed' && t.due && new Date(t.due) > new Date());
    const visible = this.filter === 'upcoming' ? upcoming : tasks.filter(t=>t.state === 'open');
    const esc = x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    this.shadowRoot.innerHTML = `<ha-card><div class="wrap"><h2>${esc(this.config.title || 'Household Tasks')}</h2>
      <div class="tabs"><button data-tab="open">Open (${tasks.filter(t=>t.state==='open').length})</button>
      <button data-tab="upcoming">Upcoming (${upcoming.length})</button></div>
      ${visible.length ? visible.map(t=>`<div class="task"><div><b>${esc(t.name)}</b><small>${esc(t.category||'')}
      ${t.due?' · '+esc(new Date(t.due).toLocaleString()):''}</small></div>
      ${t.state==='open'?`<button data-complete="${esc(t.entry)}">✓ Done</button>`:''}</div>`).join(''):'<p>No tasks in this view.</p>'}
      </div></ha-card><style>.wrap{padding:16px}h2{margin:0 0 16px;font-size:20px}
      .tabs{display:flex;gap:8px;margin-bottom:12px}.tabs button,.task button{border:1px solid var(--divider-color);
      border-radius:8px;padding:9px;background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer}
      .task{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;
      border-bottom:1px solid var(--divider-color)}small{display:block;color:var(--secondary-text-color);margin-top:4px}</style>`;
    this.shadowRoot.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{this.filter=b.dataset.tab;this.render()});
    this.shadowRoot.querySelectorAll('[data-complete]').forEach(b=>b.onclick=()=>this._hass.callService('household_tasks','complete',{entry_id:b.dataset.complete}));
  }
}
customElements.define('household-tasks-card',HouseholdTasksCard);
window.customCards=window.customCards||[];
window.customCards.push({type:'household-tasks-card',name:'Household Tasks',description:'Open and upcoming household tasks'});
