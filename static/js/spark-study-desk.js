(() => {
 if (window.initSparkStudySettings) { window.initSparkStudySettings(); return; }
 function initialize(root) {
 if (!root || root.dataset.ready || !root.getClientRects().length) return;
 root.dataset.ready = 'true';
 const status = root.querySelector('[data-study="status"]');
 const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || decodeURIComponent(document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '');
 async function api(path, options = {}) {
   const response = await fetch('/api/pwanimate/' + path, {credentials:'same-origin', cache:'no-store', ...options, headers:{'Content-Type':'application/json', 'X-CSRFToken':csrf(), ...options.headers}});
   const data = await response.json();
   if (!response.ok) throw new Error(data.detail || 'Could not complete this change.');
   return data;
 }
 function entry(target, title, body) {
   const box = document.createElement('div'); box.className='pwanimate-choice-card p-3 rounded-3 border mb-2';
   const h = document.createElement('h3'); h.className='small fw-semibold mb-1'; h.textContent=title;
   const p = document.createElement('p'); p.className='small text-muted mb-0'; p.textContent=body;
   box.append(h,p); target.appendChild(box); return box;
 }
 const hasMemory = !!root.querySelector('[data-study="memories"]');
 const hasReminders = !!root.querySelector('[data-study="reminders"]');
 const hasPlans = !!root.querySelector('[data-study="plans"]');
 async function load() {
   try {
    const [memory, reminders, plans] = await Promise.all([
      hasMemory ? api('memory/') : null,
      hasReminders ? api('reminders/') : null,
      hasPlans ? api('study-plans/') : null,
    ]);
    if (memory) {
      const enabled=root.querySelector('[data-study="enabled"]');
      if (enabled) enabled.checked = memory.enabled;
      const memories=root.querySelector('[data-study="memories"]'); memories.replaceChildren();
      memory.memories.forEach(m=>{const row=entry(memories,m.topic,m.summary); const a=document.createElement('a');a.href='/pwanimate/'+encodeURIComponent(m.conversation_id)+'/';a.textContent='Continue this chat →';a.className='btn btn-link btn-sm px-0 pb-0';row.appendChild(a);});
      if (!memory.memories.length) entry(memories,'A fresh start','After a meaningful study chat, Spark will save a short summary here.');
    }
    if (reminders) {
      const list=root.querySelector('[data-study="reminders"]');list.replaceChildren();
      reminders.reminders.filter(r=>r.status==='scheduled').forEach(r=>{const row=entry(list,r.title,new Date(r.due_at).toLocaleString()+' · '+(r.phone_scheduled?'Phone scheduling acknowledged':'Saved · phone scheduling pending'));const b=document.createElement('button');b.type='button';b.className='btn btn-link btn-sm text-danger px-0 pb-0';b.textContent='Cancel reminder';b.onclick=async()=>{try{await api('reminders/'+r.id+'/',{method:'DELETE'});await window.sparkReminders?.sync();await load();}catch(e){status.textContent=e.message;}};row.appendChild(b);});
    }
    if (plans) {
      const planList=root.querySelector('[data-study="plans"]');planList.replaceChildren();
      plans.plans.forEach(p=>entry(planList,p.title,p.sessions.map(s=>s.topic+' · '+s.minutes+' min').join(' / ')));
      if(!plans.plans.length) entry(planList,'Room for your next goal','Try asking Spark: “Create a study plan for my upcoming exam.”');
    }
   } catch(e){status.textContent=e.message;}
 }
 const enabledEl=root.querySelector('[data-study="enabled"]');
 if (enabledEl) enabledEl.onchange=async e=>{try{await api('memory/',{method:'PATCH',body:JSON.stringify({enabled:e.target.checked})});status.textContent=e.target.checked?'Memory enabled.':'Memory paused.';}catch(error){e.target.checked=!e.target.checked;status.textContent=error.message;}};
 const forgetEl=root.querySelector('[data-study="forget"]');
 if (forgetEl) forgetEl.onclick=async()=>{if(!confirm('Forget saved study memory? Your chat history will stay.'))return;try{await api('memory/',{method:'DELETE'});status.textContent='Saved memory forgotten.';await load();}catch(e){status.textContent=e.message;}};
 const reminderForm=root.querySelector('[data-study="reminder-form"]');
 if (reminderForm) reminderForm.onsubmit=async e=>{e.preventDefault();const form=e.target;const button=form.querySelector('button');button.disabled=true;try{await api('reminders/',{method:'POST',headers:{'Idempotency-Key':(form.dataset.requestKey ||= crypto.randomUUID())},body:JSON.stringify({title:form.elements.namedItem('title').value,due_at:new Date(form.elements.namedItem('due').value).toISOString(),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone})});status.textContent='Reminder saved. Checking phone scheduling…';form.reset();delete form.dataset.requestKey;await window.sparkReminders?.sync();await load();}catch(error){status.textContent=error.message;}finally{button.disabled=false;}};
 load();
 }
 window.initSparkStudySettings = function(scope = document) {
   if (scope.matches?.('.spark-study-settings')) initialize(scope);
   scope.querySelectorAll('.spark-study-settings').forEach(initialize);
 };
 document.addEventListener('htmx:afterSwap', () => window.initSparkStudySettings());
 document.addEventListener('shown.bs.modal', () => window.initSparkStudySettings());
 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => window.initSparkStudySettings());
 else window.initSparkStudySettings();
})();
