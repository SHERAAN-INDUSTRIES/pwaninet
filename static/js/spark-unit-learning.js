(() => {
  if (window.initSparkUnitLearning) { window.initSparkUnitLearning(); return; }
  function initialize(root) {
    if (root.dataset.ready || !root.getClientRects().length) return;
    root.dataset.ready='true';
    const mode=root.querySelector('[data-unit="mode"]'), status=root.querySelector('[data-unit="status"]');
    const enrolled=root.querySelector('[data-unit="enrollments"]'), records=root.querySelector('[data-unit="records"]');
    const history=root.querySelector('[data-unit="history"]');
    const csrf=()=>document.querySelector('meta[name="csrf-token"]')?.content || decodeURIComponent(document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '');
    async function api(path='', body) {
      const response=await fetch('/api/pwanimate/academic/' + path, {credentials:'same-origin',cache:'no-store',
        ...(body ? {method:path?'POST':'PATCH',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify(body)} : {})});
      const data=await response.json(); if(!response.ok) throw new Error(data.detail || 'Could not update your study record.');
      return data;
    }
    function entry(target, text) {
      const item=document.createElement('div'); item.className='pwanimate-choice-card p-3 rounded-3 border mb-2';
      const label=document.createElement('p'); label.className='small mb-1'; label.textContent=text;
      item.appendChild(label); target.appendChild(item); return item;
    }
    function button(target,label,fn) {
      const b=document.createElement('button'); b.type='button'; b.className='btn btn-outline-secondary btn-sm me-2 mt-2'; b.textContent=label;
      b.onclick=async()=>{ b.disabled=true; try { await fn(); } catch(error) { status.textContent=error.message; } finally { b.disabled=false; } };
      target.appendChild(b);
    }
    async function showRecord(id) {
      const data=await api('records/'+encodeURIComponent(id)+'/'); history.replaceChildren();
      entry(history, data.unit_name+' · '+data.term+(data.read_only?' · Read-only history':''));
      entry(history, 'Topics: '+(data.topics.join(', ') || 'No approved activity recorded yet.'));
      for(const a of data.activities) entry(history,new Date(a.occurred_at).toLocaleString()+' · '+a.kind+' · '+a.excerpt);
      entry(history,'Assessments: '+data.assessments.length+'. Study requests and self-reports do not establish mastery.');
      for(const session of data.sessions) {
        const row=entry(history, new Date(session.started_at).toLocaleString()+' · '+session.status);
        if(!data.read_only) for(const action of ['pause','resume','end']) button(row,action,async()=>{
          await api('records/'+encodeURIComponent(id)+'/',{action,session_id:session.id}); await showRecord(id);
        });
      }
      if(!data.read_only) button(history,'Start a new session',async()=>{
        if(!window.confirm('Track future activity in a new session for '+data.unit_name+'? Earlier messages will not be imported.')) return;
        const result=await api('records/'+encodeURIComponent(id)+'/',{action:'start',tracking_consent:true});
        const url=result.session.url;
        if(url?.startsWith('/') && !url.startsWith('//')) window.location.assign(url);
      });
    }
    async function load() {
      try {
        const data=await api(); mode.value=data.tracking.mode; mode.disabled=false;
        enrolled.replaceChildren(); records.replaceChildren();
        for(const item of data.enrollments) entry(enrolled,item.code+' · '+item.name+' · '+(item.term || 'No enrollment term')+
          (item.tracking_available?'':' · Tracking unavailable'));
        if(!data.enrollments.length) entry(enrolled,'No student unit enrollments are available. Set up your academic profile first.');
        for(const record of data.records) button(entry(records,record.name+' · '+record.term),'View study history',()=>showRecord(record.id));
      } catch(error) { status.textContent=error.message; }
    }
    mode.onchange=async()=>{
      mode.disabled=true;
      try { await api('',{mode:mode.value}); status.textContent='Tracking preference saved. Units need fresh authorization.'; }
      catch(error) { status.textContent=error.message; }
      await load();
    };
    load();
  }
  window.initSparkUnitLearning=()=>document.querySelectorAll('.spark-unit-learning').forEach(initialize);
  document.addEventListener('htmx:afterSwap',()=>window.initSparkUnitLearning());
  document.addEventListener('shown.bs.modal',()=>window.initSparkUnitLearning());
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',()=>window.initSparkUnitLearning());
  else window.initSparkUnitLearning();
})();
