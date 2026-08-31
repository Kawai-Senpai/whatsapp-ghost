/* ===== WhatsApp Ghost developer console ===== */
const state = { config:{}, apps:[], businesses:[], users:[], messages:[], webhooks:[], subscriptions:[], templates:[], hookPage:1,
  unread:new Map(), lastSeen:new Map(), activity:[], observer:null, observerRetry:null };
const SIM_FEED_LIMIT = 40;
let guideLanguage = 'curl';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const initials = s => (s||'?').trim().slice(0,2).toUpperCase();

async function req(url, options={}) {
  const r = await fetch(url, options);
  let data; try { data = await r.json(); } catch { data = { error: await r.text() }; }
  if (!r.ok) {
    const graph=data?.error;
    if(graph && typeof graph==='object'){
      const title=graph.error_user_title||graph.message||'Request failed';
      const detail=graph.error_user_msg||graph.error_data?.details||graph.message||r.statusText;
      const identity=[graph.code!=null?`code ${graph.code}`:'',graph.error_subcode!=null?`subcode ${graph.error_subcode}`:''].filter(Boolean).join(', ');
      const trace=graph.fbtrace_id?`Trace: ${graph.fbtrace_id}`:'';
      throw new Error([`${title}: ${detail}`,identity&&`(${identity})`,trace].filter(Boolean).join('\n'));
    }
    throw new Error(String(data?.error||data?.detail||r.statusText));
  }
  return data;
}
function toast(msg, bad=false){ const e=$('#toast'); e.textContent=msg; e.classList.toggle('bad',bad); e.classList.add('show'); clearTimeout(e._t); e._t=setTimeout(()=>e.classList.remove('show'),bad?10000:2600); }
function copyText(v){ navigator.clipboard.writeText(v); toast('Copied to clipboard'); }
function openModal(id){ $('#'+id).classList.add('open'); }
function closeModal(id){ $('#'+id).classList.remove('open'); }
function jsonHtml(value){
  const safe=esc(JSON.stringify(value,null,2));
  return safe.replace(/(&quot;(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\&])*&quot;)(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?/g,match=>{
    let cls='json-number';
    if(/^&quot;/.test(match)) cls=/:$/.test(match)?'json-key':'json-string';
    else if(/true|false/.test(match)) cls='json-boolean';
    else if(/null/.test(match)) cls='json-null';
    return `<span class="${cls}">${match}</span>`;
  });
}

/* ---- navigation ---- */
function goto(page){
  state.page = page;
  // Remembered so a reload returns to the page you were on rather than
  // dropping you back on the dashboard.
  try{ localStorage.setItem('ghost.console.page', page); }catch{}
  document.querySelectorAll('.side-link').forEach(b=>b.classList.toggle('active', b.dataset.page===page));
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active', p.id===page));
  window.scrollTo(0,0);
  if(page==='credentials') renderCredentials();
  if(page==='webhooks') loadWebhooks();
  if(page==='templates') loadTemplates();
  if(page==='guide') renderGuide();
  if(page==='simulator') loadSimActivity();
}
document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',e=>{ e.preventDefault(); goto(b.dataset.page); }));

/* ---- load everything ---- */
async function loadAll(){
  try{
    state.config = await req('/_sandbox/config');
    const [apps,biz,users,msgs] = await Promise.all([
      req('/_sandbox/apps'), req('/_sandbox/businesses'),
      req('/_sandbox/phones'), req('/_sandbox/messages?limit=500')
    ]);
    state.apps=apps.data; state.businesses=biz.data; state.users=users.data; state.messages=msgs.data;
    const numbers = state.businesses.reduce((n,b)=>n+b.phone_numbers.length,0);

    $('#base-small').textContent = state.config.base_url.replace(/^https?:\/\//,'');
    if($('#avatar-mode')) $('#avatar-mode').textContent = state.config.base_url.replace(/^https?:\/\//,'') + ' · ' + state.config.mode;
    $('#mode-foot').textContent = state.config.mode.toUpperCase();
    $('#endpoint').textContent = state.config.base_url + '/v25.0/PHONE_LOCAL/messages';
    renderMetrics();

    renderApps(); renderBusinesses(); renderUsers(); fillSelectors();
    await loadTemplates(); await loadWebhooks();
    renderCredentials();
  }catch(e){ toast(e.message,true); }
}
/* Dashboard counters and the get-started checklist, recomputed from state.
   Split out of loadAll so a live event can refresh them without re-fetching
   every collection in the console. */
function renderMetrics(){
  const numbers = state.businesses.reduce((n,b)=>n+b.phone_numbers.length,0);
  if($('#m-apps')) $('#m-apps').textContent = state.apps.length;
  if($('#m-numbers')) $('#m-numbers').textContent = numbers;
  if($('#m-users')) $('#m-users').textContent = state.users.length;
  if($('#m-messages')) $('#m-messages').textContent = state.messages.length;

  setTask('task-number', numbers>0); setTask('s-number', numbers>0);
  setTask('task-user', state.users.length>0); setTask('s-user', state.users.length>0);
  setTask('task-msg', state.messages.length>0); setTask('s-msg', state.messages.length>0);
}

function setTask(id, done){ const el=$('#'+id); if(el) el.classList.toggle('done', done); const c=el?.querySelector('.check'); if(c) c.textContent=done?'✓':''; }

/* ---- render: apps ---- */
function renderApps(){
  $('#app-id-side').textContent = state.apps[0]?.id || 'local';
  $('#app-list').innerHTML = state.apps.map(a=>`
    <div class="item">
      <div class="item-head">
        <div class="avatar"><svg class="ico" style="color:var(--fb-blue)"><use href="#i-key"/></svg></div>
        <div class="grow"><b>${esc(a.name)} <span class="badge">ACTIVE</span></b>
          <small>${esc(a.id)} · created ${new Date(a.created_at).toLocaleString()}</small></div>
        <button class="btn secondary small" onclick="rotateToken('${esc(a.id)}')">Rotate token</button>
      </div>
      <div class="field-label">Access token</div>
      <div class="secret"><code>${esc(a.access_token)}</code><button class="btn secondary small" onclick="copyText('${esc(a.access_token)}')">Copy</button></div>
      <div class="field-label">App secret</div>
      <div class="secret"><code>${esc(a.app_secret)}</code><button class="btn secondary small" onclick="copyText('${esc(a.app_secret)}')">Copy</button></div>
    </div>`).join('') || '<div class="empty">No apps yet. Create one to get an access token.</div>';
}

/* ---- render: businesses ---- */
function renderBusinesses(){
  $('#business-list').innerHTML = state.businesses.map(b=>`
    <div class="item">
      <div class="item-head">
        <div class="avatar wa">${esc(initials(b.name))}</div>
        <div class="grow"><b>${esc(b.name)} <span class="badge blue">BUSINESS</span></b>
          <small>${b.phone_numbers.length} registered sender${b.phone_numbers.length===1?'':'s'}</small></div>
        <button class="btn small" onclick='openSenderModal(${JSON.stringify(b.id)},${JSON.stringify(b.name)})'><svg><use href="#i-plus"/></svg>Add sender</button>
      </div>
      <div class="resource-path">
        <div><small>Business ID</small><code>${esc(b.business_id)}</code></div>
        <div><small>WhatsApp Business Account (WABA)</small><code>${esc(b.id)}</code></div>
        <div><small>Sender numbers</small><code>${b.phone_numbers.length}</code></div>
      </div>
      ${b.phone_numbers.map(p=>`
        <div class="subnumber">
          <div class="avatar wa" style="width:34px;height:34px;border-radius:9px"><svg class="ico" style="color:var(--green-dark)"><use href="#i-phone"/></svg></div>
          <div class="grow"><b>${esc(p.verified_name)} <span class="badge">${esc(p.quality_rating||'GREEN')}</span></b>
            <small>Business phone +${esc(p.display_phone_number)} · Phone-number ID ${esc(p.id)}</small></div>
          <button class="btn wa small" onclick="openPhoneForBusiness('${esc(p.id)}')"><svg><use href="#i-open"/></svg>Test chat</button>
          <button class="btn secondary small" onclick='editBusiness(${JSON.stringify(b.id)},${JSON.stringify(b.name)},${JSON.stringify(p.id)},${JSON.stringify(p.verified_name)},${JSON.stringify(p.display_phone_number)})'><svg><use href="#i-edit"/></svg>Edit</button>
        </div>`).join('')}
    </div>`).join('') || '<div class="empty">No businesses yet. Add one to register a sender number.</div>';
}

/* ---- render: test users ---- */
function userMatches(u, q){
  if(!q) return true;
  return (u.display_name + ' ' + u.wa_id).toLowerCase().includes(q);
}

function renderUsers(){
  const q = (state.userFilter || '').trim().toLowerCase();
  const all = state.users;
  // Unread first (whoever is waiting on a reply should never be scrolled to),
  // then most recently active, and only then by name.
  const shown = all.filter(u => userMatches(u, q))
    .sort((a,b)=> unreadFor(b.wa_id)-unreadFor(a.wa_id)
      || lastSeenFor(b.wa_id)-lastSeenFor(a.wa_id)
      || String(a.display_name||'').localeCompare(String(b.display_name||'')));
  const counter = $('#user-count');
  if(counter) counter.textContent = q ? `${shown.length} of ${all.length}` : `${all.length}`;
  const filterWrap = $('#user-filter-wrap');
  // The list is unbounded once autocreate is on, so only offer search when it helps.
  if(filterWrap) filterWrap.hidden = all.length < 8;

  if(!all.length){
    $('#user-list').innerHTML = '<div class="empty">No test customers yet. Send a message to any number and it appears here.</div>';
    return;
  }
  if(!shown.length){
    $('#user-list').innerHTML = `<div class="empty">No customers match "${esc(q)}".</div>`;
    return;
  }
  $('#user-list').innerHTML = shown.map(u=>{
    const color = u.color || '#25D366';
    const name = esc(u.display_name);
    return `
    <div class="item user-item"><div class="user-row">
      <div class="avatar wa" style="background:${esc(color)}1f;color:${esc(color)}">${esc(initials(u.display_name))}</div>
      <div class="grow user-id">
        <b title="${name}">${name}${u.auto_created ? '<span class="badge gray" title="Created automatically on first message">auto</span>' : ''}</b>
        <small title="+${esc(u.wa_id)}">+${esc(u.wa_id)}${lastSeenFor(u.wa_id)?' · '+esc(new Date(lastSeenFor(u.wa_id)).toLocaleTimeString()):''}</small>
      </div>
      <div class="user-actions">
        ${unreadFor(u.wa_id)?`<span class="unread-pill" title="${unreadFor(u.wa_id)} unread">${unreadFor(u.wa_id)>99?'99+':unreadFor(u.wa_id)}</span>`:''}
        <button class="btn wa small" onclick="openPhoneTabFor('${esc(u.wa_id)}')"><svg><use href="#i-open"/></svg>Open</button>
        <button class="btn secondary small icon-only" title="Rename or recolor ${name}" aria-label="Rename or recolor ${name}" onclick="editPhone('${esc(u.wa_id)}')"><svg><use href="#i-edit"/></svg></button>
        <button class="btn danger small icon-only" title="Delete ${name}" aria-label="Delete ${name}" onclick="deletePhone('${esc(u.wa_id)}','${name}')"><svg><use href="#i-trash"/></svg></button>
      </div>
    </div></div>`;
  }).join('');
}

function setUserFilter(value){ state.userFilter = value; renderUsers(); }

async function editPhone(wa){
  const user = state.users.find(u => u.wa_id === wa);
  if(!user) return;
  const name = prompt(`Display name for +${wa}`, user.display_name);
  if(name === null) return;
  const color = prompt('Accent color (hex, e.g. #25D366)', user.color || '#25D366');
  if(color === null) return;
  const trimmed = color.trim();
  if(!/^#[0-9a-fA-F]{6}$/.test(trimmed)){ toast('Color must be a 6-digit hex value like #25D366'); return; }
  try{
    await req(`/_sandbox/phones/${encodeURIComponent(wa)}`,{
      method:'PATCH', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({display_name:name.trim() || user.display_name, color:trimmed})
    });
    toast('Customer updated'); loadAll();
  }catch(e){ toast('Could not update customer'); }
}

/* ---- selectors ---- */
function fillSelectors(){
  const wabaOpts = state.businesses.map(b=>`<option value="${esc(b.id)}">${esc(b.name)} · ${esc(b.id)}</option>`).join('');
  $('#tpl-waba').innerHTML = wabaOpts; $('#wh-waba').innerHTML = wabaOpts;
  $('#wh-app').innerHTML = state.apps.map(a=>`<option value="${esc(a.id)}">${esc(a.name)} · ${esc(a.id)}</option>`).join('')
    || '<option value="">— create an app first —</option>';
  $('#sim-user').innerHTML = state.users.map(u=>`<option value="${esc(u.wa_id)}">${esc(u.display_name)} · +${esc(u.wa_id)}</option>`).join('')
    || '<option value="">— add a customer first —</option>';
  const bizNums=[]; state.businesses.forEach(b=>b.phone_numbers.forEach(p=>bizNums.push(`<option value="${esc(p.id)}">${esc(p.verified_name)} · +${esc(p.display_phone_number)}</option>`)));
  $('#sim-business').innerHTML = bizNums.join('') || '<option value="">— add a business first —</option>';
  const appValue=$('#guide-app')?.value, senderValue=$('#guide-sender')?.value, customerValue=$('#guide-customer')?.value;
  if($('#guide-app')) $('#guide-app').innerHTML=state.apps.map(a=>`<option value="${esc(a.id)}">${esc(a.name)} · ${esc(a.id)}</option>`).join('')||'<option value="">Default local token</option>';
  if($('#guide-sender')) $('#guide-sender').innerHTML=bizNums.join('')||'<option value="">Add a business sender first</option>';
  if($('#guide-customer')) $('#guide-customer').innerHTML=state.users.map(u=>`<option value="${esc(u.wa_id)}">${esc(u.display_name)} · +${esc(u.wa_id)}</option>`).join('')||'<option value="">Add a test customer first</option>';
  if(appValue&&[...$('#guide-app').options].some(o=>o.value===appValue)) $('#guide-app').value=appValue;
  if(senderValue&&[...$('#guide-sender').options].some(o=>o.value===senderValue)) $('#guide-sender').value=senderValue;
  if(customerValue&&[...$('#guide-customer').options].some(o=>o.value===customerValue)) $('#guide-customer').value=customerValue;
  renderGuide();
}

/* ---- templates / webhooks ---- */
async function loadTemplates(){
  if(!state.config.access_token) return;
  let all=[];
  for(const b of state.businesses){
    // _wabaId is kept alongside the display name because deleting a template
    // is scoped to its business account: name alone is not unique across WABAs.
    try{ const d=await req(`/v25.0/${b.id}/message_templates`,{headers:{Authorization:'Bearer '+state.config.access_token}}); all.push(...d.data.map(t=>({...t,_waba:b.name,_wabaId:b.id}))); }catch{}
  }
  state.templates=all;
  $('#template-list').innerHTML = all.map(t=>`
    <div class="item"><div class="item-head">
      <div class="avatar"><svg class="ico" style="color:var(--fb-blue)"><use href="#i-template"/></svg></div>
      <div class="grow"><b>${esc(t.name)} <span class="badge">${esc(t.status)}</span></b>
        <small>${esc(t.language)} · ${esc(t.category)} · ${esc(t._waba)}</small></div>
      <button class="btn danger small" onclick='deleteTemplate(${JSON.stringify(t._wabaId)},${JSON.stringify(t.name)})'>Delete</button></div>
      <div style="margin-top:10px;color:var(--muted)">${esc(t.components?.find(c=>c.type==='BODY')?.text||'')}</div>
    </div>`).join('') || '<div class="empty">No templates yet.</div>';
}

async function deleteTemplate(wabaId,name){
  if(!confirm(`Delete template "${name}"?

Messages already sent with it keep their stored payload, but new sends naming it will fail.`)) return;
  try{
    await req(`/v25.0/${encodeURIComponent(wabaId)}/message_templates?name=${encodeURIComponent(name)}`,
      {method:'DELETE',headers:{Authorization:'Bearer '+state.config.access_token}});
    toast('Template deleted');
    await loadTemplates();
  }catch(x){ toast(x.message,true); }
}
async function loadWebhooks(){
  const [events,subscriptions] = await Promise.all([req('/_sandbox/webhooks'),req('/_sandbox/webhook-subscriptions')]);
  state.webhooks=events.data; state.subscriptions=subscriptions.data;
  $('#wh-subscriptions').textContent=state.subscriptions.filter(s=>s.active).length;
  $('#wh-total').textContent=state.webhooks.length;
  $('#wh-delivered').textContent=state.webhooks.filter(w=>w.status==='delivered').length;
  $('#wh-failed').textContent=state.webhooks.filter(w=>w.status==='failed').length;
  const unrouted=$('#wh-unrouted'); if(unrouted) unrouted.textContent=state.webhooks.filter(w=>w.status==='unrouted').length;
  populateWebhookFilters();
  $('#subscription-list').innerHTML=state.subscriptions.filter(s=>s.active).map(s=>`
    <div class="subscription-row"><span class="badge">ACTIVE</span><div class="grow"><b>${esc(s.business_name||s.waba_id)}</b><small>${esc(s.callback_url)} · ${esc(s.app_name||s.app_id||'Local app')}</small></div><code>${esc(s.waba_id)}</code><button class="btn danger small" onclick='unsubscribeWebhook(${JSON.stringify(s.waba_id)},${JSON.stringify(s.app_id||"")},${JSON.stringify(s.business_name||s.waba_id)})'>Unsubscribe</button></div>`).join('')||'<div class="empty">No callback is subscribed. Unrouted events are still retained in history.</div>';
  renderUnsubscribedWarning();
  renderWebhookHistory();
}

function renderUnsubscribedWarning(){
  const box=$('#hook-unsubscribed'); if(!box) return;
  const subscribed=new Set((state.subscriptions||[]).map(s=>s.waba_id));
  const missing=(state.businesses||[]).filter(b=>!subscribed.has(b.id));
  if(!missing.length){ box.hidden=true; box.innerHTML=''; return; }
  box.hidden=false;
  box.innerHTML=`<b>${missing.length} account${missing.length>1?'s have':' has'} no subscribed callback.</b>
    Events for ${missing.length>1?'them':'it'} are stored as <span class="badge amber">UNROUTED</span> and never sent.
    <div class="unsub-list">${missing.map(b=>`<span class="unsub-chip">${esc(b.name)} · ${esc(b.id)}</span>`).join('')}</div>`;
}
function webhookFacets(w){
  const payload=w.request_body||{}, change=payload.entry?.[0]?.changes?.[0], value=change?.value||{};
  return {
    payload, value,
    waba: payload.entry?.[0]?.id || '',
    phone: value.metadata?.phone_number_id || '',
    kind: value.messages?.[0] ? 'inbound' : value.statuses?.[0] ? 'status' : ''
  };
}

function populateWebhookFilters(){
  const wabas=new Set(), phones=new Set();
  state.webhooks.forEach(w=>{ const f=webhookFacets(w); if(f.waba) wabas.add(f.waba); if(f.phone) phones.add(f.phone); });
  const fill=(sel,values,label)=>{
    const el=$(sel); if(!el) return;
    const current=el.value;
    el.innerHTML=`<option value="all">${label}</option>`+[...values].sort().map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
    if([...values].includes(current)) el.value=current;
  };
  fill('#hook-waba', wabas, 'All accounts');
  fill('#hook-phone', phones, 'All numbers');
}

function clearWebhookFilters(){
  ['#hook-filter','#hook-waba','#hook-phone','#hook-event'].forEach(id=>{ const el=$(id); if(el) el.value='all'; });
  const search=$('#hook-search'); if(search) search.value='';
  renderWebhookHistory(1);
}

const STATUS_ORDER={failed:0,pending:1,unrouted:2,delivered:3};

function sortWebhooks(items,mode){
  const byNewest=(a,b)=>new Date(b.created_at)-new Date(a.created_at);
  if(mode==='oldest') return items.sort((a,b)=>new Date(a.created_at)-new Date(b.created_at));
  if(mode==='attempts') return items.sort((a,b)=>(b.attempt_count||0)-(a.attempt_count||0)||byNewest(a,b));
  if(mode==='status') return items.sort((a,b)=>
    (STATUS_ORDER[a.status]??9)-(STATUS_ORDER[b.status]??9)||byNewest(a,b));
  return items.sort(byNewest);
}

function renderPager(total,page,size){
  const pager=$('#hook-pager'); if(!pager) return;
  const pages=Math.max(1,Math.ceil(total/size));
  if(pages<=1){ pager.innerHTML=''; return; }
  const btn=(label,target,disabled,current)=>
    `<button class="btn secondary small${current?' current':''}" ${disabled?'disabled':''} onclick="renderWebhookHistory(${target})">${label}</button>`;
  // Window the page numbers so 40 pages do not render 40 buttons.
  const win=[]; const from=Math.max(1,page-2), to=Math.min(pages,page+2);
  if(from>1) win.push(btn('1',1,false,page===1), from>2?'<span class="pager-gap">…</span>':'');
  for(let i=from;i<=to;i++) win.push(btn(String(i),i,false,i===page));
  if(to<pages) win.push(to<pages-1?'<span class="pager-gap">…</span>':'', btn(String(pages),pages,false,page===pages));
  pager.innerHTML=`${btn('Prev',page-1,page<=1)}${win.join('')}${btn('Next',page+1,page>=pages)}`
    +`<span class="pager-info">Page ${page} of ${pages}</span>`;
}

function renderWebhookHistory(page){
  const filter=$('#hook-filter')?.value||'all', query=($('#hook-search')?.value||'').toLowerCase();
  const wabaFilter=$('#hook-waba')?.value||'all', phoneFilter=$('#hook-phone')?.value||'all';
  const eventFilter=$('#hook-event')?.value||'all';
  const active = filter!=='all'||wabaFilter!=='all'||phoneFilter!=='all'||eventFilter!=='all'||!!query;
  const items=state.webhooks.filter(w=>{
    const f=webhookFacets(w);
    if(filter!=='all' && w.status!==filter) return false;
    if(wabaFilter!=='all' && f.waba!==wabaFilter) return false;
    if(phoneFilter!=='all' && f.phone!==phoneFilter) return false;
    if(eventFilter!=='all' && f.kind!==eventFilter) return false;
    if(query && !JSON.stringify(w).toLowerCase().includes(query)) return false;
    return true;
  });
  const counter=$('#hook-count');
  if(counter) counter.textContent = active ? `${items.length} of ${state.webhooks.length}` : `${state.webhooks.length}`;
  const clear=$('#hook-clear'); if(clear) clear.hidden = !active;

  sortWebhooks(items, $('#hook-sort')?.value||'newest');
  const size=parseInt($('#hook-size')?.value||'25',10);
  const pages=Math.max(1,Math.ceil(items.length/size));
  state.hookPage = Math.min(Math.max(1, page||state.hookPage||1), pages);
  const start=(state.hookPage-1)*size;
  const pageItems=items.slice(start,start+size);
  renderPager(items.length,state.hookPage,size);
  const list=$('#webhook-list'); if(list) list.scrollTop=0;

  $('#webhook-list').innerHTML=pageItems.map(w=>{
    const f=webhookFacets(w), payload=f.payload, value=f.value;
    // The event's own subject (an inbound message, or a message-status update)
    // is distinct from whether OUR delivery to the subscriber succeeded.
    const subject=value.messages?.[0] ? `Inbound ${value.messages[0].type||'message'}`
      : value.statuses?.[0] ? `Message ${value.statuses[0].status}` : (w.event_type||'Event');
    const waba=f.waba||'—', phone=f.phone||'—';
    const badge=w.status==='delivered'?'':w.status==='failed'?'red':'amber';
    const subscribedWabas=new Set((state.subscriptions||[]).map(x=>x.waba_id));
    const unroutedHint = w.status==='unrouted' && waba!=='—' && !subscribedWabas.has(waba)
      ? `No callback is subscribed for <b>${esc(waba)}</b>, so this event was stored but never sent. Subscriptions are per WhatsApp Business Account: a callback registered for another WABA does not receive these.`
      : 'No network attempt was made because the event was unrouted.';
    const attempts=(w.attempts||[]).map(a=>`<div class="attempt-row"><span class="badge ${a.error?'red':''}">#${a.attempt_number}</span><div><b>${a.status_code??'Network error'}</b><small>${new Date(a.requested_at).toLocaleString()}${a.completed_at?' → '+new Date(a.completed_at).toLocaleTimeString():''}</small>${a.error?`<div class="event-error">${esc(a.error)}</div>`:''}${a.response_body?`<pre>${esc(a.response_body)}</pre>`:''}</div></div>`).join('')||`<div class="empty">${unroutedHint}</div>`;
    return `<div class="item"><div class="item-head">
      <div class="avatar"><svg class="ico" style="color:var(--fb-blue)"><use href="#i-webhook"/></svg></div>
      <div class="grow"><b>${esc(subject)} <span class="badge ${badge}" title="Delivery of this event to your endpoint">${esc(w.status.toUpperCase())}</span></b><small>${new Date(w.created_at).toLocaleString()} · ${esc(w.id)}</small></div>
      <button class="btn secondary small" onclick="copyWebhook('${esc(w.id)}')">Copy JSON</button>${w.destination_url?`<button class="btn secondary small" onclick="replay('${esc(w.id)}')">Replay</button>`:''}</div>
      ${w.status==='unrouted' && waba!=='—' && !subscribedWabas.has(waba) ? `<div class="unrouted-note">Not delivered: <b>${esc(waba)}</b> has no subscribed callback. <button class="btn secondary small" onclick="openModal('webhook-modal')">Subscribe a URL</button></div>` : ''}
      <div class="event-meta"><div><small>WABA</small><b>${esc(waba)}</b></div><div><small>Phone-number ID</small><b>${esc(phone)}</b></div><div><small>Attempts / HTTP</small><b>${w.attempt_count} / ${esc(w.last_status_code??'—')}</b></div><div><small>Destination</small><b title="${esc(w.destination_url||'Unrouted')}">${esc(w.destination_url||'Unrouted')}</b></div></div>
      <details><summary style="cursor:pointer;color:var(--fb-blue);font-weight:600">View formatted request JSON, signature and response</summary>
        <div class="field-label">Request body</div><div class="json-view">${jsonHtml(payload)}</div>
        <div class="field-label">X-Hub-Signature-256</div><div class="secret"><code>${esc(w.signature)}</code><button class="btn secondary small" onclick="copyText('${esc(w.signature)}')">Copy</button></div>
        <div class="field-label">Delivery attempt history</div><div class="attempt-list">${attempts}</div>
        ${w.last_error?`<div class="event-error">${esc(w.last_error)}</div>`:''}${w.last_response_body?`<div class="event-response json-view">${esc(w.last_response_body)}</div>`:''}
      </details></div>`;
  }).join('')|| (state.webhooks.length
    ? '<div class="empty">No webhook history matches these filters.</div>'
    : '<div class="empty">No webhook events yet. Send or receive a message to generate one.</div>');
}
function copyWebhook(id){const item=state.webhooks.find(w=>w.id===id);if(item)copyText(JSON.stringify(item.request_body,null,2));}

/* ---- live integration guide ---- */
function selectedGuideResources(){
  const phoneId=$('#guide-sender')?.value||state.businesses[0]?.phone_numbers[0]?.id||'';
  const business=state.businesses.find(b=>b.phone_numbers.some(p=>p.id===phoneId));
  const phone=business?.phone_numbers.find(p=>p.id===phoneId);
  const app=state.apps.find(a=>a.id===$('#guide-app')?.value)||state.apps[0];
  return {phoneId,business,phone,app,customer:$('#guide-customer')?.value||state.users[0]?.wa_id||''};
}
function renderGuide(){
  if(!$('#guide-send-code')||!state.config.base_url) return;
  const {phoneId,business,phone,app,customer}=selectedGuideResources();
  const base=state.config.base_url, token=app?.access_token||state.config.access_token||'YOUR_LOCAL_TOKEN';
  const secret=app?.app_secret||'YOUR_APP_SECRET', sender=phoneId||'PHONE_NUMBER_ID', recipient=customer||'CUSTOMER_NUMBER';
  const url=`${base}/v25.0/${sender}/messages`;
  const body={messaging_product:'whatsapp',to:recipient,type:'text',text:{body:'Hello from WhatsApp Ghost!'}};
  const compact=JSON.stringify(body), pretty=JSON.stringify(body,null,2);
  const samples={
    curl:`curl -X POST "${url}" \\\n  -H "Authorization: Bearer ${token}" \\\n  -H "Content-Type: application/json" \\\n  -d '${compact}'`,
    powershell:`$headers = @{ Authorization = "Bearer ${token}" }\n$body = '${compact}'\nInvoke-RestMethod -Method Post -Uri "${url}" \`\n  -Headers $headers -ContentType "application/json" -Body $body`,
    python:`import requests\n\nresponse = requests.post(\n    "${url}",\n    headers={"Authorization": "Bearer ${token}"},\n    json=${pretty.replace(/^/gm,'    ').trimStart()}\n)\nresponse.raise_for_status()\nprint(response.json())`,
    javascript:`const response = await fetch("${url}", {\n  method: "POST",\n  headers: {\n    "Authorization": "Bearer ${token}",\n    "Content-Type": "application/json"\n  },\n  body: JSON.stringify(${pretty.replace(/^/gm,'  ').trimStart()})\n});\nconsole.log(await response.json());`
  };
  $('#guide-base').textContent=base; $('#guide-token').textContent=token; $('#guide-phone-id').textContent=sender;
  $('#guide-send-code').textContent=samples[guideLanguage];
  $('#guide-webhook-code').textContent=`# FastAPI receiver with Meta-compatible verification and signature checks\nimport hashlib, hmac\nfrom fastapi import FastAPI, HTTPException, Query, Request\nfrom fastapi.responses import PlainTextResponse\n\napp = FastAPI()\nVERIFY_TOKEN = "choose-a-verify-token"\nAPP_SECRET = "${secret}"\n\n@app.get("/webhook")\ndef verify(\n    mode: str = Query(alias="hub.mode"),\n    token: str = Query(alias="hub.verify_token"),\n    challenge: str = Query(alias="hub.challenge"),\n):\n    if mode == "subscribe" and token == VERIFY_TOKEN:\n        return PlainTextResponse(challenge)\n    raise HTTPException(403)\n\n@app.post("/webhook")\nasync def receive(request: Request):\n    raw = await request.body()\n    expected = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()\n    supplied = request.headers.get("X-Hub-Signature-256", "")\n    if not hmac.compare_digest(expected, supplied):\n        raise HTTPException(401, "Invalid signature")\n    payload = await request.json()\n    print(payload)\n    return {"ok": True}`;
  $('#guide-template-code').textContent=`curl -X POST "${url}" \\\n  -H "Authorization: Bearer ${token}" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({messaging_product:'whatsapp',to:recipient,type:'template',template:{name:'hello_world',language:{code:'en_US'},components:[{type:'body',parameters:[{type:'text',text:'Tester'}]}]}})}'`;
  const siblings=business?.phone_numbers||[];
  $('#guide-waba-name').textContent=business?`${business.name} · ${business.id}`:'Create a business first';
  $('#guide-sender-list').innerHTML=siblings.map(p=>`<span class="sender-chip">${esc(p.verified_name)} · ${esc(p.id)}</span>`).join('')||'<span class="sender-chip">No sender yet</span>';
  $('#guide-multi-code').textContent=siblings.map(p=>`POST ${base}/v25.0/${p.id}/messages  # ${p.verified_name}`).join('\n')||`POST ${base}/v25.0/PHONE_NUMBER_ID/messages`;
}
function guideTab(event,language){guideLanguage=language;document.querySelectorAll('.code-tabs button').forEach(b=>b.classList.toggle('active',b===event.currentTarget));renderGuide();}
function copyGuide(id){copyText($('#'+id)?.textContent||'');}
function openGuidePhone(){const {phoneId,customer}=selectedGuideResources();if(!customer){toast('Add a test customer first',true);return;}window.open(phoneUrl(customer,phoneId),'ghost-phone-'+customer+'-'+phoneId);}
function openGuideAddSender(){const {business}=selectedGuideResources();if(!business){toast('Add a business first',true);goto('resources');return;}openSenderModal(business.id,business.name);}

/* ---- actions ---- */
async function createApp(e){ e.preventDefault();
  try{ await req('/_sandbox/apps',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#app-name').value})});
    closeModal('app-modal'); e.target.reset(); toast('App and credentials created'); loadAll(); }catch(x){ toast(x.message,true); } }
async function rotateToken(id){ try{ const d=await req(`/_sandbox/apps/${id}/rotate-token`,{method:'POST'}); copyText(d.access_token); toast('Token rotated and copied'); loadAll(); }catch(e){ toast(e.message,true); } }
async function createBusiness(e){ e.preventDefault();
  try{ await req('/_sandbox/businesses',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:$('#biz-name').value,verified_name:$('#biz-verified').value,display_phone_number:$('#biz-phone').value})});
    closeModal('business-modal'); e.target.reset(); toast('Business, WABA and sender created'); loadAll(); }catch(x){ toast(x.message,true); } }

function openSenderModal(waba,name){$('#sender-waba').value=waba;$('#sender-business-name').textContent=name;$('#sender-verified').value=name;openModal('sender-modal');}
async function createSender(e){e.preventDefault();try{
  await req(`/_sandbox/businesses/${$('#sender-waba').value}/phone-numbers`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({verified_name:$('#sender-verified').value,display_phone_number:$('#sender-number').value})});
  closeModal('sender-modal');e.target.reset();toast('Sender added to this WABA');await loadAll();
}catch(x){toast(x.message,true);}}

function editBusiness(waba,name,phoneId,verified,number){
  $('#eb-waba').value=waba; $('#eb-phone-id').value=phoneId;
  $('#eb-name').value=name; $('#eb-verified').value=verified; $('#eb-number').value=number;
  openModal('edit-business-modal');
}
async function saveBusiness(e){ e.preventDefault();
  try{
    await req(`/_sandbox/businesses/${$('#eb-waba').value}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#eb-name').value})});
    await req(`/_sandbox/phone-numbers/${$('#eb-phone-id').value}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({verified_name:$('#eb-verified').value,display_phone_number:$('#eb-number').value})});
    closeModal('edit-business-modal'); toast('Business updated'); loadAll();
  }catch(x){ toast(x.message,true); } }

async function createTemplate(e){ e.preventDefault();
  try{ const w=$('#tpl-waba').value;
    const text=$('#tpl-body').value;
    const indexes=[...text.matchAll(/\{\{(\d+)\}\}/g)].map(m=>Number(m[1]));
    const sampleCount=indexes.length?Math.max(...indexes):0;
    const bodyComponent={type:'BODY',text};
    if(sampleCount) bodyComponent.example={body_text:[[...Array(sampleCount)].map((_,i)=>`sample_${i+1}`)]};
    await req(`/v25.0/${w}/message_templates`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+state.config.access_token},
      body:JSON.stringify({name:$('#tpl-name').value,language:$('#tpl-language').value,category:$('#tpl-category').value,components:[bodyComponent],_sandbox_auto_approve:true})});
    closeModal('template-modal'); e.target.reset(); toast('Template approved locally'); loadTemplates(); }catch(x){ toast(x.message,true); } }
async function createWebhook(e){ e.preventDefault();
  const app=state.apps.find(a=>a.id===$('#wh-app').value);
  if(!app){ toast('Select a developer app for webhook signing',true); return; }
  try{ await req(`/v25.0/${$('#wh-waba').value}/subscribed_apps`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+app.access_token},
      body:JSON.stringify({callback_url:$('#wh-url').value,verify_token:$('#wh-verify').value||undefined})});
    closeModal('webhook-modal'); e.target.reset(); toast('Webhook verified and subscribed'); loadWebhooks(); }catch(x){ toast(x.message,true); } }
async function unsubscribeWebhook(waba,appId,name){
  if(!confirm(`Unsubscribe the webhook for ${name}?\nNew events will remain unrouted until another callback is subscribed.`)) return;
  const app=state.apps.find(a=>a.id===appId);
  const headers=app?{Authorization:'Bearer '+app.access_token}:{};
  try{ await req(`/v25.0/${encodeURIComponent(waba)}/subscribed_apps`,{method:'DELETE',headers}); toast('Webhook unsubscribed'); await loadWebhooks(); }
  catch(e){ toast(e.message,true); }
}
async function createPhone(e){ e.preventDefault();
  try{ await req('/_sandbox/phones',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({wa_id:$('#user-phone').value,display_name:$('#user-name').value})});
    closeModal('phone-modal'); e.target.reset(); toast('Test customer created'); loadAll(); }catch(x){ toast(x.message,true); } }
async function deletePhone(wa, name){
  if(!confirm(`Delete test customer ${name} (+${wa})?\nThis removes the customer and its chat history.`)) return;
  try{ await req(`/_sandbox/phones/${encodeURIComponent(wa)}`,{method:'DELETE'}); toast('Customer deleted'); loadAll(); }
  catch(e){ toast(e.message,true); }
}
async function replay(id){ try{ await req(`/_sandbox/webhooks/${id}/replay`,{method:'POST'}); toast('Delivery replayed'); loadWebhooks(); }catch(e){ toast(e.message,true); } }
async function advanceClock(){ try{ const d=await req('/_sandbox/clock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'advance',value:'25h'})}); toast('Clock is now '+d.now); }catch(e){ toast(e.message,true); } }

/* ---- phone tab ---- */
function phoneUrl(wa, phoneId){
  const p = new URLSearchParams(); p.set('phone', wa); if(phoneId) p.set('business', phoneId);
  return '/phone?'+p.toString();
}
function openPhoneTab(){
  const wa=$('#sim-user').value, biz=$('#sim-business').value;
  if(!wa){ toast('Add a test customer first',true); return; }
  window.open(phoneUrl(wa,biz),'ghost-phone-'+wa);
}
function openPhoneTabFor(wa){
  const biz = state.businesses[0]?.phone_numbers[0]?.id || '';
  window.open(phoneUrl(wa,biz),'ghost-phone-'+wa);
}
function openPhoneForBusiness(phoneId){
  const wa=state.users[0]?.wa_id;
  if(!wa){ toast('Create a test customer first',true); goto('simulator'); return; }
  window.open(phoneUrl(wa,phoneId),'ghost-phone-'+wa+'-'+phoneId);
}

/* ---- modal backdrop close + boot ---- */
document.querySelectorAll('.modal-back').forEach(m=>m.addEventListener('click',e=>{ if(e.target===m) m.classList.remove('open'); }));
if(location.pathname==='/guide') goto('guide');
else if(location.hash==='#simulator') goto('simulator');
try{
  const remembered = localStorage.getItem('ghost.console.page');
  if(remembered && document.getElementById(remembered)) goto(remembered);
}catch{ /* private mode: start on the default page */ }
loadAll().then(()=>gsWarmIndex())
  .then(loadSimUnread).then(renderUsers)
  .then(loadSimActivity).then(connectConsoleObserver);

/* ---- credentials ---- */
const CRED_FORMATS = ['.env', 'curl', 'Python', 'Node', 'JSON'];
let credFormat = '.env';

function credRow(key, value, hint){
  if(!value) return '';
  return `<div class="cred-row"><div class="k">${esc(key)}</div>
    <div class="v"><code>${esc(value)}</code>
      <button class="cred-copy" onclick="copyText(this.previousElementSibling.textContent)">Copy</button></div>
    ${hint?`<span class="hint">${esc(hint)}</span>`:''}</div>`;
}

/* The selected sender, app and customer, falling back to the first of each so
   the page is useful before anything is chosen. */
function credSelection(){
  const senders = [];
  state.businesses.forEach(b => b.phone_numbers.forEach(p => senders.push({...p, waba: b})));
  const sender = senders.find(s => s.id === $('#cred-sender')?.value) || senders[0];
  const app = state.apps.find(a => a.id === $('#cred-app')?.value) || state.apps[0];
  const customer = state.users.find(u => u.wa_id === $('#cred-customer')?.value) || state.users[0];
  const subscription = state.subscriptions.find(s => s.active && sender && s.waba_id === sender.waba.id)
    || state.subscriptions.find(s => s.active);
  return {senders, sender, app, customer, subscription};
}

function renderCredentials(){
  const {senders, sender, app, customer, subscription} = credSelection();
  const base = state.config.base_url || location.origin;
  const token = app?.access_token || state.config.access_token;

  // keep the pickers populated without clobbering an existing choice
  const fill = (sel, html, value) => {
    const el = $(sel); if(!el) return;
    el.innerHTML = html; if(value) el.value = value;
  };
  fill('#cred-sender', senders.map(s=>`<option value="${esc(s.id)}">${esc(s.verified_name)} · +${esc(s.display_phone_number)}</option>`).join('')
    || '<option value="">— add a business first —</option>', sender?.id);
  fill('#cred-app', state.apps.map(a=>`<option value="${esc(a.id)}">${esc(a.name)} · ${esc(a.id)}</option>`).join('')
    || '<option value="">— default local token —</option>', app?.id);
  fill('#cred-customer', state.users.map(u=>`<option value="${esc(u.wa_id)}">${esc(u.display_name)} · +${esc(u.wa_id)}</option>`).join('')
    || '<option value="">— add a customer first —</option>', customer?.wa_id);

  $('#cred-grid').innerHTML = [
    credRow('Base URL', base, 'Replaces https://graph.facebook.com'),
    credRow('API version', 'v25.0', 'Any version path is accepted'),
    credRow('Access token', token, 'Send as: Authorization: Bearer <token>'),
    credRow('Phone number ID', sender?.id, 'The {phone_id} in /{version}/{phone_id}/messages'),
    credRow('WhatsApp Business Account ID', sender?.waba?.id, 'Owns templates and subscriptions'),
    credRow('Business ID', sender?.waba?.business_id),
    credRow('Business phone number', sender && ('+' + sender.display_phone_number), 'The sender your customers see'),
    credRow('App ID', app?.id),
    credRow('App secret', app?.app_secret, 'Belongs to this app alone; validates X-Hub-Signature-256 for webhooks it subscribed'),
    credRow('Webhook verify token', subscription?.verify_token || '— none subscribed —', 'The token you chose when subscribing; echoed back during GET /webhook verification'),
    credRow('Webhook callback URL', subscription?.callback_url || '— not subscribed —', 'Where signed events are delivered'),
    credRow('Test customer', customer && ('+' + customer.wa_id), 'Recipient "to" value; omit the +'),
  ].join('') || '<div class="empty">Nothing configured yet.</div>';

  $('#cred-tabs').innerHTML = CRED_FORMATS.map(f =>
    `<button class="cred-tab${f===credFormat?' active':''}" onclick="setCredFormat('${esc(f)}')">${esc(f)}</button>`).join('');
  $('#cred-snippet').textContent = credSnippet(base, token, sender, customer, app, subscription);
}

function setCredFormat(format){ credFormat = format; renderCredentials(); }

function credSnippet(base, token, sender, customer, app, subscription){
  const phoneId = sender?.id || 'PHONE_LOCAL';
  const waba = sender?.waba?.id || 'WABA_LOCAL';
  const to = customer?.wa_id || '15550002001';
  const secret = app?.app_secret || 'local-app-secret';
  const verify = subscription?.verify_token || 'local-verify-token';

  if(credFormat === '.env'){
    return `WHATSAPP_GRAPH_BASE_URL=${base}
WHATSAPP_API_VERSION=v25.0
WHATSAPP_ACCESS_TOKEN=${token}
WHATSAPP_PHONE_NUMBER_ID=${phoneId}
WHATSAPP_BUSINESS_ACCOUNT_ID=${waba}
WHATSAPP_APP_SECRET=${secret}
WHATSAPP_WEBHOOK_VERIFY_TOKEN=${verify}`;
  }
  if(credFormat === 'curl'){
    return `curl -X POST "${base}/v25.0/${phoneId}/messages" \
  -H "Authorization: Bearer ${token}" \
  -H "Content-Type: application/json" \
  -d '${JSON.stringify({messaging_product:'whatsapp',to,type:'template',template:{name:'hello_world',language:{code:'en_US'},components:[{type:'body',parameters:[{type:'text',text:'Tester'}]}]}})}'`;
  }
  if(credFormat === 'Python'){
    return `import httpx

BASE_URL = "${base}"
ACCESS_TOKEN = "${token}"
PHONE_NUMBER_ID = "${phoneId}"

response = httpx.post(
    f"{BASE_URL}/v25.0/{PHONE_NUMBER_ID}/messages",
    headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
    json={
        "messaging_product": "whatsapp",
        "to": "${to}",
        "type": "text",
        "text": {"body": "Hello from the sandbox"},
    },
)
print(response.json())`;
  }
  if(credFormat === 'Node'){
    return `const BASE_URL = ${JSON.stringify(base)};
const ACCESS_TOKEN = ${JSON.stringify(token)};
const PHONE_NUMBER_ID = ${JSON.stringify(phoneId)};

const response = await fetch(\`\${BASE_URL}/v25.0/\${PHONE_NUMBER_ID}/messages\`, {
  method: 'POST',
  headers: {
    Authorization: \`Bearer \${ACCESS_TOKEN}\`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    messaging_product: 'whatsapp',
    to: '${to}',
    type: 'text',
    text: { body: 'Hello from the sandbox' },
  }),
});
console.log(await response.json());`;
  }
  return JSON.stringify({
    base_url: base, api_version: 'v25.0', access_token: token,
    phone_number_id: phoneId, whatsapp_business_account_id: waba,
    business_id: sender?.waba?.business_id, app_id: app?.id, app_secret: secret,
    webhook_verify_token: verify, webhook_callback_url: subscription?.callback_url || null,
    test_customer: to,
  }, null, 2);
}

/* ---- global search ---- */
/* Templates and subscriptions are normally fetched only when their page is
   opened. Search spans the whole console, so warm that data once at startup
   without touching the DOM those page renderers own. */
async function gsWarmIndex(){
  try{
    const subs = await req('/_sandbox/webhook-subscriptions');
    state.subscriptions = subs.data;
  }catch{}
  if(state.config.access_token && !(state.templates||[]).length){
    const all=[];
    for(const b of state.businesses){
      try{
        const d=await req(`/v25.0/${b.id}/message_templates`,{headers:{Authorization:'Bearer '+state.config.access_token}});
        all.push(...d.data.map(t=>({...t,_waba:b.name})));
      }catch{}
    }
    state.templates=all;
  }
}

const GS_PAGES = [
  {page:'overview',    icon:'i-home',     title:'Dashboard',            sub:'Overview, metrics and getting-started tasks'},
  {page:'credentials', icon:'i-key',      title:'Credentials',          sub:'IDs, tokens and secrets used by this sandbox'},
  {page:'setup',       icon:'i-gear',     title:'Production setup',     sub:'Set up accounts to contact customers'},
  {page:'resources',   icon:'i-numbers',  title:'API Setup & Numbers',  sub:'Business accounts and registered senders'},
  {page:'apps',        icon:'i-key',      title:'Apps & Tokens',        sub:'Developer apps, app secrets and access tokens'},
  {page:'templates',   icon:'i-template', title:'Message Templates',    sub:'Approved local templates with positional variables'},
  {page:'webhooks',    icon:'i-webhook',  title:'Webhooks',             sub:'Signed events, delivery attempts and replay'},
  {page:'guide',       icon:'i-guide',    title:'Integration Guide',    sub:'Connect an application in minutes'},
  {page:'simulator',   icon:'i-phone',    title:'Phone Simulator',      sub:'Open a full WhatsApp Web experience'}
];

function gsIndex(){
  const out = [];
  GS_PAGES.forEach(p => out.push({
    group:'Pages', icon:p.icon, title:p.title, sub:p.sub,
    hay:p.title + ' ' + p.sub + ' ' + p.page, run:()=>goto(p.page)
  }));

  state.businesses.forEach(b => {
    out.push({
      group:'Businesses', icon:'i-numbers', title:b.name,
      sub:b.id + ' · ' + b.phone_numbers.length + ' sender' + (b.phone_numbers.length===1?'':'s'),
      hay:b.name + ' ' + b.id, run:()=>goto('resources')
    });
    b.phone_numbers.forEach(ph => out.push({
      group:'Sender numbers', icon:'i-phone',
      title:ph.verified_name + ' · +' + ph.display_phone_number,
      sub:ph.id + ' · ' + b.name,
      hay:ph.verified_name + ' ' + ph.display_phone_number + ' ' + ph.id + ' ' + b.name,
      run:()=>goto('resources')
    }));
  });

  state.apps.forEach(a => out.push({
    group:'Apps', icon:'i-key', title:a.name, sub:a.id,
    hay:a.name + ' ' + a.id, run:()=>goto('apps')
  }));

  (state.templates||[]).forEach(t => {
    const body = (t.components||[]).find(c=>c.type==='BODY');
    out.push({
      group:'Templates', icon:'i-template', title:t.name,
      sub:t.status + ' · ' + t.language + ' · ' + t._waba,
      hay:[t.name,t.status,t.language,t.category,t._waba,(body&&body.text)||''].join(' '),
      run:()=>goto('templates')
    });
  });

  state.users.forEach(u => out.push({
    group:'Test customers', icon:'i-phone', title:u.display_name,
    sub:'+' + u.wa_id + ' · open WhatsApp Web',
    hay:u.display_name + ' ' + u.wa_id, run:()=>openPhoneTabFor(u.wa_id)
  }));

  (state.subscriptions||[]).filter(s=>s.active).forEach(s => out.push({
    group:'Webhook endpoints', icon:'i-webhook', title:s.callback_url || 'Subscription',
    sub:[s.business_name||s.waba_id, s.app_name||s.app_id].filter(Boolean).join(' · ') || 'Configured endpoint',
    hay:[s.callback_url||'', s.waba_id||'', s.business_name||'', s.app_name||'', s.app_id||'', 'webhook subscription endpoint'].join(' '),
    run:()=>goto('webhooks')
  }));

  return out;
}

function gsScore(entry, q){
  const hay = entry.hay.toLowerCase(), title = entry.title.toLowerCase();
  if(title.startsWith(q)) return 0;
  if(title.includes(q)) return 1;
  if(hay.includes(q)) return 2;
  return -1;
}
function gsMark(text, q){
  const t = String(text == null ? '' : text);
  const i = t.toLowerCase().indexOf(q);
  if(i < 0) return esc(t);
  return esc(t.slice(0,i)) + '<mark>' + esc(t.slice(i,i+q.length)) + '</mark>' + esc(t.slice(i+q.length));
}

let gsMatches = [], gsActive = -1;

function gsRender(){
  const box = $('#gs-results'), q = $('#gs-input').value.trim().toLowerCase();
  $('#gs-clear').hidden = !q;
  if(!q){ gsClose(); return; }

  const seen = new Set();
  const scored = gsIndex()
    .map((e,i) => ({e:e, s:gsScore(e,q), i:i}))
    .filter(x => {
      if(x.s < 0) return false;
      const key = x.e.group + '\u0000' + x.e.title + '\u0000' + (x.e.sub||'');
      if(seen.has(key)) return false;   // collapse duplicates (e.g. one endpoint per WABA)
      seen.add(key);
      return true;
    });

  // Rank groups by their strongest match, then keep each group contiguous.
  // Contiguity matters: the rendered group headers must line up with the
  // data-i indices, otherwise arrow keys and clicks select the wrong row.
  const bestByGroup = new Map();
  scored.forEach(x => {
    const cur = bestByGroup.get(x.e.group);
    if(cur === undefined || x.s < cur) bestByGroup.set(x.e.group, x.s);
  });

  gsMatches = scored
    .sort((a,b) => {
      const ga = bestByGroup.get(a.e.group), gb = bestByGroup.get(b.e.group);
      if(ga !== gb) return ga - gb;
      if(a.e.group !== b.e.group) return a.e.group < b.e.group ? -1 : 1;
      if(a.s !== b.s) return a.s - b.s;
      return a.i - b.i;
    })
    .slice(0,20)
    .map(x => x.e);

  if(!gsMatches.length){
    box.innerHTML = '<div class="gs-empty">No matches for &quot;' + esc(q) + '&quot;</div>';
  } else {
    let html = '', group = null;
    gsMatches.forEach((e,i) => {
      if(e.group !== group){ group = e.group; html += '<div class="gs-group">' + esc(group) + '</div>'; }
      html += '<div class="gs-item" role="option" data-i="' + i + '">' +
        '<span class="gs-ico"><svg class="ico"><use href="#' + esc(e.icon) + '"/></svg></span>' +
        '<span class="gs-main"><b>' + gsMark(e.title,q) + '</b><small>' + gsMark(e.sub||'',q) + '</small></span>' +
      '</div>';
    });
    html += '<div class="gs-hint"><span><kbd>&uarr;</kbd><kbd>&darr;</kbd> navigate</span>' +
            '<span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span></div>';
    box.innerHTML = html;
  }
  gsActive = gsMatches.length ? 0 : -1;
  gsHighlight();
  box.hidden = false;
  $('#gs-input').setAttribute('aria-expanded','true');
}

function gsHighlight(){
  document.querySelectorAll('#gs-results .gs-item').forEach(el=>{
    const on = Number(el.dataset.i) === gsActive;
    el.classList.toggle('active', on);
    if(on) el.scrollIntoView({block:'nearest'});
  });
}
function gsClose(){
  const box = $('#gs-results');
  box.hidden = true; box.innerHTML = '';
  gsMatches = []; gsActive = -1;
  $('#gs-input').setAttribute('aria-expanded','false');
}
function gsChoose(i){
  const e = gsMatches[i];
  if(!e) return;
  gsClose();
  $('#gs-input').blur();
  e.run();
}

$('#gs-input').addEventListener('input', gsRender);
$('#gs-input').addEventListener('focus', ()=>{ if($('#gs-input').value.trim()) gsRender(); });
$('#gs-input').addEventListener('keydown', e=>{
  if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    if(!gsMatches.length) return;
    e.preventDefault();
    gsActive = (gsActive + (e.key === 'ArrowDown' ? 1 : -1) + gsMatches.length) % gsMatches.length;
    gsHighlight();
  } else if(e.key === 'Enter'){
    if(gsActive >= 0){ e.preventDefault(); gsChoose(gsActive); }
  } else if(e.key === 'Escape'){
    if(!$('#gs-results').hidden){ e.preventDefault(); gsClose(); }
    else { $('#gs-input').value=''; $('#gs-clear').hidden=true; $('#gs-input').blur(); }
  }
});
$('#gs-results').addEventListener('mousedown', e=>{
  const item = e.target.closest('.gs-item');
  if(item){ e.preventDefault(); gsChoose(Number(item.dataset.i)); }
});
$('#gs-results').addEventListener('mousemove', e=>{
  const item = e.target.closest('.gs-item');
  if(item && Number(item.dataset.i) !== gsActive){ gsActive = Number(item.dataset.i); gsHighlight(); }
});
$('#gs-clear').addEventListener('click', ()=>{
  $('#gs-input').value=''; gsClose(); $('#gs-clear').hidden=true; $('#gs-input').focus();
});
document.addEventListener('click', e=>{ if(!e.target.closest('#global-search')) gsClose(); });
document.addEventListener('keydown', e=>{
  const el = document.activeElement;
  const typing = el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName || '');
  if((e.key === '/' && !typing) || ((e.ctrlKey||e.metaKey) && e.key.toLowerCase() === 'k')){
    e.preventDefault(); $('#gs-input').focus(); $('#gs-input').select();
  }
});

/* ---- avatar menu ---- */
function avatarOpen(open){
  $('#avatar-pop').hidden = !open;
  $('#avatar-btn').setAttribute('aria-expanded', String(open));
}
$('#avatar-btn').addEventListener('click', e=>{ e.stopPropagation(); avatarOpen($('#avatar-pop').hidden); });
$('#avatar-pop').addEventListener('click', e=>{ if(e.target.closest('button')) avatarOpen(false); });
document.addEventListener('click', e=>{ if(!e.target.closest('#avatar-menu')) avatarOpen(false); });
document.addEventListener('keydown', e=>{ if(e.key === 'Escape') avatarOpen(false); });

function copyBaseUrl(){ copyText(state.config.base_url || location.origin); }
function reloadConsole(){ loadAll().then(()=>gsWarmIndex()); toast('Reloading sandbox data'); }

/* ===== live console =====
   Everything below exists so the console never needs a manual refresh. It
   listens on /_sandbox/observer, the same firehose the phone page uses, which
   carries every sandbox event tagged with the wa_id it belongs to. */

function unreadFor(wa){ return state.unread.get(wa) || 0; }

async function loadSimUnread(){
  // Derived from message status server-side, so it is correct on a cold load
  // and cannot drift from the read receipts the sandbox reports to webhooks.
  try{
    const d = await req('/_sandbox/unread');
    const totals = new Map();
    for(const row of (d.data||[])) totals.set(row.wa_id, (totals.get(row.wa_id)||0) + row.unread);
    state.unread = totals;
    // Recency is tracked separately because unread alone cannot order the list:
    // once everything is read every customer sits at 0 and the order freezes
    // alphabetically, which is exactly the "not sorting" complaint.
    const seen = new Map();
    for(const row of (d.activity||[])){
      const at = new Date(row.last_at).getTime();
      if(at > (seen.get(row.wa_id)||0)) seen.set(row.wa_id, at);
    }
    state.lastSeen = seen;
  }catch{ /* keep the previous counts rather than blanking every badge */ }
}

function lastSeenFor(wa){ return state.lastSeen.get(wa) || 0; }

function simMobileName(wa){
  const u = (state.users||[]).find(x => x.wa_id === wa);
  return u ? (u.display_name || wa) : wa;
}
function simBusinessName(phoneId){
  for(const b of (state.businesses||[])){
    const match = (b.phone_numbers||[]).find(p => p.id === phoneId);
    if(match) return match.verified_name;
  }
  return phoneId || 'Business';
}

/* Summarise one stored message row for the activity list. */
function simSummary(message){
  const type = message.message_type || message.type || 'text';
  const payload = message.payload || message;
  if(type === 'text'){
    const text = payload.text;
    return typeof text === 'string' ? text : (text?.body || payload.body || '');
  }
  if(type === 'template') return (payload.template?.name || payload.name || 'template');
  if(type === 'button') return payload.button?.text || payload.button?.payload || 'Button reply';
  if(type === 'reaction') return payload.reaction?.emoji || 'Reaction';
  return '[' + type + ']';
}

function pushSimActivity(entry){
  state.activity.unshift(entry);
  if(state.activity.length > SIM_FEED_LIMIT) state.activity.length = SIM_FEED_LIMIT;
  renderSimActivity();
}

function renderSimActivity(){
  const box = $('#sim-activity');
  if(!box) return;
  if(!state.activity.length){
    box.innerHTML = '<div class="empty">Nothing yet. Any message to or from a test customer appears here as it happens.</div>';
    return;
  }
  // The customer is always the subject; "to"/"from" carries the direction, so
  // the two names keep their positions and the column reads down cleanly.
  box.innerHTML = state.activity.map(item=>`
    <button type="button" class="sim-act ${item.inbound?'inbound':'outbound'}"
            data-open-wa="${esc(item.wa)}" data-open-business="${esc(item.phoneId)}"
            title="Open this conversation in the phone simulator">
      <span class="sim-act-rail" aria-hidden="true"></span>
      <div class="sim-act-main">
        <div class="sim-act-row">
          <b class="sim-act-who">${esc(simMobileName(item.wa))}</b>
          <span class="sim-act-dir">${item.inbound?'to':'from'} ${esc(simBusinessName(item.phoneId))}</span>
          <span class="sim-act-type">${esc(item.type)}</span>
          <span class="sim-act-time">${esc(new Date(item.at).toLocaleTimeString())}</span>
        </div>
        <div class="sim-act-body">${esc(item.text || '(no body)')}</div>
      </div>
    </button>`).join('');
}

/* Seed the list on first open so it is not empty before anything new arrives. */
async function loadSimActivity(){
  try{
    const d = await req('/_sandbox/messages?limit=' + SIM_FEED_LIMIT);
    state.activity = (d.data||[]).map(m=>({
      wa: m.direction === 'inbound' ? m.sender_id : m.recipient_id,
      phoneId: m.direction === 'inbound' ? m.recipient_id : m.sender_id,
      inbound: m.direction === 'inbound',
      type: m.message_type,
      text: simSummary(m),
      at: m.created_at,
    }));
  }catch{ state.activity = []; }
  renderSimActivity();
}

/* Delegated so the rows keep working across every live re-render. Opens the
   exact pair the row describes - that customer, that business - rather than
   the first business, which is what openPhoneTabFor falls back to. */
document.addEventListener('click', event=>{
  const row = event.target.closest('.sim-act[data-open-wa]');
  if(!row) return;
  const wa = row.dataset.openWa;
  const biz = row.dataset.openBusiness || state.businesses[0]?.phone_numbers[0]?.id || '';
  window.open(phoneUrl(wa, biz), 'ghost-phone-' + wa);
});

function simLive(live){
  const pill = $('#sim-live');
  if(!pill) return;
  pill.classList.toggle('on', !!live);
  pill.title = live ? 'Live' : 'Reconnecting';
}

function connectConsoleObserver(){
  if(state.observer){ state.observer.onclose = null; state.observer.close(); }
  if(state.observerRetry) clearTimeout(state.observerRetry);
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  state.observer = new WebSocket(`${scheme}://${location.host}/_sandbox/observer`);
  state.observer.onopen = ()=>simLive(true);
  state.observer.onmessage = async event=>{
    let data; try{ data = JSON.parse(event.data); }catch{ return; }
    await handleConsoleEvent(data);
  };
  state.observer.onclose = ()=>{
    simLive(false);
    state.observerRetry = setTimeout(connectConsoleObserver, 1500);
  };
}

async function handleConsoleEvent(data){
  // A customer appearing is exactly what used to need a reload, so the roster
  // is re-fetched rather than patched: one small request that cannot drift.
  if(['phone_created','phone_deleted','phone_updated'].includes(data.event)){
    try{
      const [users, biz] = await Promise.all([req('/_sandbox/phones'), req('/_sandbox/businesses')]);
      state.users = users.data;
      state.businesses = biz.data;
    }catch{ return; }
    await loadSimUnread();
    renderUsers();
    renderBusinesses();
    renderSimActivity();
    renderMetrics();
    fillSelectors();
    // Credentials embeds the sender and WABA ids, so it goes stale too.
    if(state.page === 'credentials') renderCredentials();
    return;
  }
  if(data.event === 'message' && data.wa_id){
    const message = data.message || {};
    const inbound = data.direction === 'inbound'
      || (message.direction||'') === 'inbound' || !!message.from;
    pushSimActivity({
      wa: data.wa_id,
      // An inbound Meta payload carries no recipient, so the event's own
      // phone_number_id is the only source for the business it reached.
      phoneId: data.phone_number_id || message.phone_number_id
        || (inbound ? message.recipient_id : message.sender_id) || '',
      inbound,
      type: message.message_type || message.type || 'text',
      text: simSummary(message),
      at: message.created_at || Date.now(),
    });
    // The dashboard message counter and the "Send a message" checklist item
    // both read state.messages, so it has to grow as messages arrive.
    state.messages.unshift(message);
    await loadSimUnread();
    renderUsers();
    renderMetrics();
    // A webhook is queued for every message, so the history is now stale.
    if(state.page === 'webhooks') loadWebhooks();
    return;
  }
  if(data.event === 'status' || data.event === 'read'){
    await loadSimUnread();
    renderUsers();
    if(state.page === 'webhooks') loadWebhooks();
  }
}
