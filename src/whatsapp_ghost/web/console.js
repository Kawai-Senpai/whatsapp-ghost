/* ===== WhatsApp Ghost developer console ===== */
// Declared before state, which reads it for the initial pagination cap.
const SIM_FEED_LIMIT = 40;
const state = { config:{}, apps:[], businesses:[], users:[], messages:[], webhooks:[], subscriptions:[], templates:[], hookPage:1,
  unread:new Map(), lastSeen:new Map(), activity:[], observer:null, observerRetry:null,
  actMore:false, actBefore:null, actLoading:false, loadedCap:SIM_FEED_LIMIT };  // recent-messages pagination
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
  if(page==='webhooks'){ showSkeleton('#webhook-list', 4); loadWebhooks(); }
  if(page==='templates'){ showSkeleton('#template-list', 3); loadTemplates(); }
  if(page==='guide') renderGuide();
  if(page==='simulator'){ showSkeleton('#sim-activity', 4); loadSimActivity(); renderAnalytics(); loadAnalytics(); }
  if(page==='overview'){
    renderAnalytics(); loadAnalytics();
    // The dashboard carries the live feed and the notifications now, so it has
    // to seed them the same way the simulator page does.
    if(!state.activity.length){ showSkeleton('#dash-activity', 3); loadSimActivity(); }
    renderDashNumbers(); renderNotifications();
  }
}
document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',e=>{ e.preventDefault(); goto(b.dataset.page); }));

/* Placeholder rows for a list whose fetch is still in flight. An empty
   container is indistinguishable from a broken page, which is what made the
   console feel stuck on every navigation. */
function skeletonRows(count){
  return '<div class="sk-rows">' + Array.from({length:count}, ()=>
    '<div class="sk-row"><div class="skeleton sk-avatar"></div>'
    + '<div class="sk-lines"><div class="skeleton sk-line w40"></div>'
    + '<div class="skeleton sk-line w70"></div></div></div>').join('') + '</div>';
}

function showSkeleton(selector, count){
  const box = document.querySelector(selector);
  // Only ever replace an empty container: a reload of an already-populated
  // list should not blink its contents away.
  if(box && !box.children.length) box.innerHTML = skeletonRows(count||3);
}

/* ---- load everything ---- */
async function loadAll(){
  ['#business-list','#app-list','#template-list','#user-list','#webhook-list','#sim-activity']
    .forEach(selector=>showSkeleton(selector, 3));
  try{
    state.config = await req('/_sandbox/config');
    // Boot fetches only what the first paint needs. It used to also pull 500
    // messages and the ENTIRE webhook delivery log - unbounded, one extra query
    // per delivery - and await both before showing anything, which is why
    // /console took minutes on a sandbox that had been running a while.
    const [apps,biz,users,stats] = await Promise.all([
      req('/_sandbox/apps'), req('/_sandbox/businesses'),
      req('/_sandbox/phones'), req('/_sandbox/stats').catch(()=>null)
    ]);
    state.apps=apps.data; state.businesses=biz.data; state.users=users.data;
    state.stats = stats || {};

    $('#base-small').textContent = state.config.base_url.replace(/^https?:\/\//,'');
    if($('#avatar-mode')) $('#avatar-mode').textContent = state.config.base_url.replace(/^https?:\/\//,'') + ' · ' + state.config.mode;
    $('#mode-foot').textContent = state.config.mode.toUpperCase();
    $('#endpoint').textContent = state.config.base_url + '/v25.0/PHONE_LOCAL/messages';
    renderMetrics();

    renderApps(); renderBusinesses(); renderUsers(); fillSelectors();
    renderCredentials(); renderQuickCreds();
    // Everything below is either a different page or a secondary panel, so it
    // fills in behind the painted dashboard instead of holding it up.
    loadTemplates();
    loadWebhookStats();
    if(state.page === 'webhooks') loadWebhooks();
  }catch(e){ toast(e.message,true); }
}

/* Delivery counters without the deliveries. The four numbers in the webhook
   header are the only thing the dashboard needs from that table. */
async function loadWebhookStats(){
  try{
    const d = await req('/_sandbox/webhooks/stats');
    state.webhookStats = d;
    // Eight newest deliveries for the dashboard panel. Cheap enough to refresh
    // alongside the counters, and independent of the webhook page's own paging.
    if(d.total){
      req('/_sandbox/webhooks?limit=8')
        .then(page=>{ state.dashHooks = page.data || []; renderNotifications(); })
        .catch(()=>{});
    }else{
      state.dashHooks = [];
    }
    const set = (id, value) => { const el=$(id); if(el) el.textContent = value; };
    set('#wh-total', d.total); set('#wh-delivered', d.delivered);
    set('#wh-failed', d.failed); set('#wh-unrouted', d.unrouted);
    renderNotifications();
  }catch{ /* the page still works without the counters */ }
}
/* Dashboard counters and the get-started checklist, recomputed from state.
   Split out of loadAll so a live event can refresh them without re-fetching
   every collection in the console. */
function renderMetrics(){
  const numbers = state.businesses.reduce((n,b)=>n+b.phone_numbers.length,0);
  // Message count comes from COUNT(*), not from the length of whatever page of
  // messages happens to be loaded, which silently pegged this tile at its cap.
  const messages = state.stats?.messages ?? state.messages.length;
  if($('#m-apps')) $('#m-apps').textContent = state.apps.length;
  if($('#m-numbers')) $('#m-numbers').textContent = numbers;
  if($('#m-users')) $('#m-users').textContent = state.users.length;
  if($('#m-messages')) $('#m-messages').textContent = messages.toLocaleString();

  setTask('task-number', numbers>0); setTask('s-number', numbers>0);
  setTask('task-user', state.users.length>0); setTask('s-user', state.users.length>0);
  setTask('task-msg', messages>0); setTask('s-msg', messages>0);
}

/* The dashboard's message counter has to keep moving as traffic arrives, but
   re-running COUNT(*) per message would be worse than the problem. */
function bumpMessageCount(by=1){
  if(state.stats) state.stats.messages = (state.stats.messages||0) + by;
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
  renderDashNumbers();
  const q = (state.userFilter || '').trim().toLowerCase();
  const all = state.users;
  // Unread first (whoever is waiting on a reply should never be scrolled to),
  // then most recently active, and only then by name.
  // Favourites lead, exactly as in the dashboard list and the simulator's own
  // roster: three views of one set of numbers must not disagree on order.
  const shown = all.filter(u => userMatches(u, q))
    .sort((a,b)=> Number(!!b.starred)-Number(!!a.starred)
      || unreadFor(b.wa_id)-unreadFor(a.wa_id)
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
    <div class="item user-item ${u.starred?'starred':''}"><div class="user-row">
      <button class="user-star ${u.starred?'on':''}" data-star-wa="${esc(u.wa_id)}" aria-pressed="${!!u.starred}"
              title="${u.starred?'Remove '+name+' from favourites':'Add '+name+' to favourites'}"
              aria-label="${u.starred?'Remove from favourites':'Add to favourites'}"><svg class="ico"><use href="#i-star"/></svg></button>
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
  // Templates are fetched per business account, one request each, so this pane
  // is blank for longer the more senders exist. The placeholder goes up before
  // the token check, or an early return leaves an empty pane behind.
  showSkeleton('#template-list', 3);
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
  // Definitions arrive after the first paint now, and a template message can
  // only be rendered to its real body once its definition is known, so any
  // feed already on screen is rebuilt with the names resolved.
  if(state.activity?.length) loadSimActivity();
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
// One page of history at a time. The whole log used to come down on every
// console load; the counters now come from /_sandbox/webhooks/stats instead, so
// this only has to cover what is actually being read.
const WEBHOOK_PAGE = 200;

async function loadWebhooks(){
  const [events,subscriptions] = await Promise.all([
    req('/_sandbox/webhooks?limit='+WEBHOOK_PAGE), req('/_sandbox/webhook-subscriptions')]);
  state.webhooks=events.data; state.subscriptions=subscriptions.data;
  state.hookMore = !!events.has_more; state.hookBefore = events.next_before || null;
  state.dashHooks = state.webhooks.slice(0, 8);
  $('#wh-subscriptions').textContent=state.subscriptions.filter(s=>s.active).length;
  loadWebhookStats();
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
  // The stored total and the loaded window are different numbers now that
  // history is paged, and conflating them made the count look wrong.
  const stored = state.webhookStats?.total;
  if(counter) counter.textContent = active
    ? `${items.length} of ${state.webhooks.length} loaded`
    : (state.hookMore && stored ? `${state.webhooks.length} of ${stored.toLocaleString()}` : `${state.webhooks.length}`);
  const clear=$('#hook-clear'); if(clear) clear.hidden = !active;
  const older=$('#hook-more-wrap'); if(older) older.hidden = !state.hookMore;

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

/* Older deliveries on demand, keyset-paged like the message feeds. */
async function loadMoreWebhooks(){
  if(!state.hookMore || state.hookLoading || !state.hookBefore) return;
  state.hookLoading = true;
  const button = $('#hook-more');
  if(button){ button.disabled = true; button.innerHTML = '<span class="busy-dot"></span> Loading'; }
  try{
    const d = await req('/_sandbox/webhooks?limit='+WEBHOOK_PAGE+'&before='+encodeURIComponent(state.hookBefore));
    const seen = new Set(state.webhooks.map(w=>w.id));
    for(const item of (d.data||[])) if(!seen.has(item.id)) state.webhooks.push(item);
    state.hookMore = !!d.has_more; state.hookBefore = d.next_before || null;
    populateWebhookFilters();
  }catch(e){ toast(e.message, true); }
  finally{
    state.hookLoading = false;
    if(button){ button.disabled = false; button.textContent = 'Load older deliveries'; }
    renderWebhookHistory();
  }
}

/* Clearing the delivery log. A long-running sandbox accumulates tens of
   thousands of these, and they are debugging noise once they are old. */
async function clearWebhookHistory(){
  const total = state.webhookStats?.total ?? state.webhooks.length;
  if(!confirm(`Delete all ${Number(total).toLocaleString()} stored webhook deliveries?\nThis cannot be undone. Subscriptions are not affected.`)) return;
  try{
    const d = await req('/_sandbox/webhooks',{method:'DELETE'});
    toast(`Cleared ${Number(d.deleted||0).toLocaleString()} deliveries`);
    state.webhooks=[]; state.hookMore=false; state.hookBefore=null;
    await loadWebhookStats();
    renderWebhookHistory(1);
  }catch(e){ toast(e.message,true); }
}
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
  .then(loadSimActivity).then(loadAnalytics).then(connectConsoleObserver);

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

/* Fill a template's positional {{n}} parameters from what was actually sent,
   so the feed shows the text the recipient saw rather than a bare name. */
function templateParamValue(param){
  if(!param || typeof param !== 'object') return '';
  if(param.type === 'currency') return param.currency?.fallback_value ?? '';
  if(param.type === 'date_time') return param.date_time?.fallback_value ?? '';
  return param.text ?? '';
}

function templateDefinition(name, language){
  const all = Array.isArray(state.templates) ? state.templates : [];
  return all.find(t => t.name === name && (!language || t.language === language))
      || all.find(t => t.name === name);
}

function renderedTemplateBody(sent){
  const name = sent?.name;
  if(!name) return '';
  const definition = templateDefinition(name, sent.language?.code);
  const body = (definition?.components || []).find(c => (c.type||'').toUpperCase() === 'BODY');
  if(!body || typeof body.text !== 'string') return '';
  const values = ((sent.components || []).find(c => (c.type||'').toLowerCase() === 'body')?.parameters || [])
    .map(templateParamValue);
  return body.text.replace(/\{\{(\d+)\}\}/g, (match, index) => {
    const value = values[Number(index) - 1];
    return value === undefined || value === '' ? match : value;
  }).replace(/\s+/g, ' ').trim();
}

/* Summarise one stored message row for the activity list. Templates return
   both halves: the row shows the name as a tag AND the text it rendered to,
   because the name alone says nothing about what was actually sent. */
function simSummary(message){
  const type = message.message_type || message.type || 'text';
  const payload = message.payload || message;
  if(type === 'text'){
    const text = payload.text;
    return typeof text === 'string' ? text : (text?.body || payload.body || '');
  }
  if(type === 'template'){
    const sent = payload.template || payload;
    return renderedTemplateBody(sent) || sent.name || 'template';
  }
  if(type === 'button') return payload.button?.text || payload.button?.payload || 'Button reply';
  if(type === 'reaction') return payload.reaction?.emoji || 'Reaction';
  const media = payload[type];
  if(media && (media.caption || media.filename)) return media.caption || media.filename;
  return '[' + type + ']';
}

/* The template's own name, kept beside the rendered text rather than instead
   of it: the name is what you grep the code for, the text is what was sent. */
function simTemplateName(message){
  const type = message.message_type || message.type;
  if(type !== 'template') return '';
  const payload = message.payload || message;
  return (payload.template || payload).name || '';
}

function pushSimActivity(entry){
  state.activity.unshift(entry);
  // The cap has to rise with what has been paged in, or every arrival would
  // trim off the oldest loaded row and the list could never actually grow.
  // state.loadedCap tracks the high-water mark of deliberately loaded rows.
  state.loadedCap = Math.max(state.loadedCap || SIM_FEED_LIMIT, state.activity.length);
  if(state.activity.length > state.loadedCap) state.activity.length = state.loadedCap;
  renderSimActivity();
}

function renderSimActivity(){
  renderDashActivity();
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
          ${item.template?`<span class="sim-act-tpl" title="Template name">${esc(item.template)}</span>`:''}
          <span class="sim-act-time">${esc(new Date(item.at).toLocaleTimeString())}</span>
        </div>
        <div class="sim-act-body">${esc(item.text || '(no body)')}</div>
      </div>
    </button>`).join('')
    + (state.actMore
      ? `<div class="sim-more-wrap"><button type="button" class="btn secondary small" id="sim-more">Load older messages</button></div>`
      : '');
}

/* ===== dashboard quick access =====
   The simulator is what this tool is mostly opened for, so the three things
   people went looking for - what just happened, whether webhooks are landing,
   and which numbers exist - are on the landing page too. They reuse the
   simulator's own state, so nothing extra is fetched to fill them. */
const DASH_FEED_LIMIT = 12;

/* The dashboard copy carries its own class name. Sharing `.sim-act` made the
   selector ambiguous across two feeds - the hidden dashboard row won, and every
   query for "the activity row" resolved to something that is not on screen. */
function activityRowHtml(item, compact){
  const cls = compact ? 'dash-act compact' : 'sim-act';
  return `
    <button type="button" class="${cls} ${item.inbound?'inbound':'outbound'}"
            data-open-wa="${esc(item.wa)}" data-open-business="${esc(item.phoneId)}"
            title="Open this conversation in the phone simulator">
      <span class="sim-act-rail" aria-hidden="true"></span>
      <div class="sim-act-main">
        <div class="sim-act-row">
          <b class="sim-act-who">${esc(simMobileName(item.wa))}</b>
          <span class="sim-act-dir">${item.inbound?'to':'from'} ${esc(simBusinessName(item.phoneId))}</span>
          <span class="sim-act-type">${esc(item.type)}</span>
          ${item.template?`<span class="sim-act-tpl" title="Template name">${esc(item.template)}</span>`:''}
          <span class="sim-act-time">${esc(new Date(item.at).toLocaleTimeString())}</span>
        </div>
        <div class="sim-act-body">${esc(item.text || '(no body)')}</div>
      </div>
    </button>`;
}

function renderDashActivity(){
  const box = $('#dash-activity');
  if(!box) return;
  if(!state.activity.length){
    box.innerHTML = '<div class="empty">Nothing yet. Any message to or from a test customer appears here as it happens.</div>';
    return;
  }
  box.innerHTML = state.activity.slice(0, DASH_FEED_LIMIT).map(item=>activityRowHtml(item, true)).join('');
}

function renderNotifications(){
  const stats = state.webhookStats;
  const box = $('#dash-hook-stats');
  if(box){
    box.innerHTML = !stats ? '' : `
      <div class="hook-stat"><small>Total</small><b>${Number(stats.total||0).toLocaleString()}</b></div>
      <div class="hook-stat ok"><small>Delivered</small><b>${Number(stats.delivered||0).toLocaleString()}</b></div>
      <div class="hook-stat warn"><small>Unrouted</small><b>${Number(stats.unrouted||0).toLocaleString()}</b></div>
      <div class="hook-stat bad"><small>Failed</small><b>${Number(stats.failed||0).toLocaleString()}</b></div>`;
  }
  const list = $('#dash-notifications');
  if(!list) return;
  // Its own small window, not the webhook page's: the dashboard needs eight
  // rows, and making it wait for (or trigger) a 200-row page would put the
  // delivery log back on the critical path this change just took it off.
  const items = (state.dashHooks && state.dashHooks.length ? state.dashHooks : (state.webhooks||[])).slice(0, 8);
  if(!items.length){
    list.innerHTML = stats && stats.total
      ? '<div class="empty">Loading recent deliveries…</div>'
      : '<div class="empty">No webhook events yet. Send a message to generate one.</div>';
    return;
  }
  list.innerHTML = items.map(w=>{
    const facets = webhookFacets(w), value = facets.value;
    const subject = value.messages?.[0] ? `Inbound ${value.messages[0].type||'message'}`
      : value.statuses?.[0] ? `Message ${value.statuses[0].status}` : (w.event_type||'Event');
    const tone = w.status==='delivered' ? 'ok' : w.status==='failed' ? 'bad' : 'warn';
    return `<button type="button" class="dash-hook ${tone}" data-page="webhooks">
      <span class="dash-hook-dot"></span>
      <span class="dash-hook-main">
        <span class="dash-hook-title">${esc(subject)}</span>
        <span class="dash-hook-sub">${esc(w.status)}${w.last_status_code?' · HTTP '+esc(w.last_status_code):''} · ${esc(facets.phone||facets.waba||'—')}</span>
      </span>
      <span class="dash-hook-time">${esc(new Date(w.created_at).toLocaleTimeString())}</span>
    </button>`;
  }).join('');
}

function setDashUserFilter(value){ state.dashFilter = value; renderDashNumbers(); }

function renderDashNumbers(){
  const box = $('#dash-numbers');
  if(!box) return;
  const query = (state.dashFilter||'').trim().toLowerCase();
  const all = state.users || [];
  if(!all.length){
    box.innerHTML = '<div class="empty">No test numbers yet. Add one, or message any number and it is created automatically.</div>';
    return;
  }
  // Same order as the simulator's own roster, so the two never disagree:
  // favourites, then unread, then most recently active, then by name.
  const shown = all.filter(u => userMatches(u, query)).sort((a,b)=>
    Number(!!b.starred)-Number(!!a.starred)
    || unreadFor(b.wa_id)-unreadFor(a.wa_id)
    || lastSeenFor(b.wa_id)-lastSeenFor(a.wa_id)
    || String(a.display_name||'').localeCompare(String(b.display_name||'')));
  if(!shown.length){
    box.innerHTML = `<div class="empty">No number matches "${esc(query)}".</div>`;
    return;
  }
  box.innerHTML = shown.map(u=>{
    const color = u.color || '#25D366';
    const count = unreadFor(u.wa_id);
    return `<div class="dash-number ${u.starred?'starred':''}">
      <button type="button" class="dash-number-open" data-open-wa="${esc(u.wa_id)}"
              title="Open ${esc(u.display_name||u.wa_id)} in the phone simulator">
        <span class="avatar wa" style="background:${esc(color)}1f;color:${esc(color)}">${esc(initials(u.display_name))}</span>
        <span class="dash-number-main">
          <span class="dash-number-name">${esc(u.display_name)}${u.auto_created?'<span class="badge gray">auto</span>':''}</span>
          <span class="dash-number-id">+${esc(u.wa_id)}</span>
        </span>
        ${count?`<span class="dash-unread">${count>99?'99+':count}</span>`:''}
      </button>
      <button type="button" class="dash-number-star ${u.starred?'on':''}" data-star-wa="${esc(u.wa_id)}"
              aria-pressed="${!!u.starred}" title="${u.starred?'Remove from favourites':'Add to favourites'}"
              aria-label="${u.starred?'Remove from favourites':'Add to favourites'}"><svg class="ico"><use href="#i-star"/></svg></button>
    </div>`;
  }).join('');
}

/* Favourite a test number. Persisted on the customer so the simulator's roster
   and this list stay in the same order. */
async function toggleStar(wa){
  const user = (state.users||[]).find(u=>u.wa_id===wa);
  if(!user) return;
  const next = !user.starred;
  user.starred = next;
  renderDashNumbers(); renderUsers();
  try{
    await req(`/_sandbox/phones/${encodeURIComponent(wa)}`,{
      method:'PATCH', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({starred: next}),
    });
  }catch{
    user.starred = !next;
    renderDashNumbers(); renderUsers();
    toast('Could not update favourites', true);
  }
}

document.addEventListener('click', event=>{
  const star = event.target.closest('[data-star-wa]');
  if(!star) return;
  event.preventDefault(); event.stopPropagation();
  toggleStar(star.dataset.starWa);
});

/* The two values every integration attempt needs, beside the endpoint they are
   used with. Masked until asked for: the console is often on a shared screen. */
function renderQuickCreds(){
  const box = $('#quick-creds');
  if(!box) return;
  const token = state.apps[0]?.access_token || state.config.access_token || '';
  const sender = state.businesses[0]?.phone_numbers[0]?.id || 'PHONE_LOCAL';
  const waba = state.businesses[0]?.id || 'WABA_LOCAL';
  const rows = [
    {label:'Access token', value:token, secret:true},
    {label:'Phone-number ID', value:sender},
    {label:'WhatsApp Business Account', value:waba},
  ];
  box.innerHTML = rows.map(row=>`
    <div class="quick-cred">
      <small>${esc(row.label)}</small>
      <code class="${row.secret?'masked':''}" data-secret="${row.secret?'1':''}">${esc(row.secret ? maskToken(row.value) : row.value)}</code>
      ${row.secret?`<button class="btn ghost small icon-only" title="Show or hide" aria-label="Show or hide"
         onclick="toggleSecret(this)"><svg><use href="#i-search"/></svg></button>`:''}
      <button class="btn secondary small icon-only" title="Copy ${esc(row.label)}" aria-label="Copy ${esc(row.label)}"
        onclick="copyText(${JSON.stringify(row.value)})"><svg><use href="#i-copy"/></svg></button>
    </div>`).join('');
}

function maskToken(value){
  const text = String(value||'');
  return text.length > 12 ? text.slice(0,6) + '•'.repeat(12) + text.slice(-4) : text;
}

function toggleSecret(button){
  const code = button.parentElement.querySelector('code');
  const token = state.apps[0]?.access_token || state.config.access_token || '';
  const hidden = code.classList.toggle('masked');
  code.textContent = hidden ? maskToken(token) : token;
}

/* ===== cross-number analytics =====
   The per-chat view answers "when did this conversation happen". This answers
   the question it cannot: which numbers got what, across the whole sandbox.
   One aggregate request, rendered identically into the dashboard and the
   simulator page, so the two can never drift apart. */
const DAY_START = 6, DAY_END = 18;      // local hours counted as daylight
const ANALYTICS_TARGETS = ['dash', 'sim'];

function hourLabel(hour){
  const suffix = hour < 12 ? 'am' : 'pm';
  return (hour % 12 === 0 ? 12 : hour % 12) + suffix;
}
function barHeight(count, peak){
  // A non-zero bucket always keeps a visible sliver, or a quiet hour beside a
  // busy one reads as no data rather than as little data.
  return count ? Math.max(4, Math.round((count / peak) * 100)) : 0;
}
function shortDate(day){
  return new Date(day + 'T00:00:00').toLocaleDateString([], {month:'short', day:'numeric'});
}

function analyticsHourHistogram(byHour){
  const peak = Math.max(...byHour, 1);
  return `
    <div class="an-card">
      <div class="an-card-head"><b>By hour of day</b>
        <span class="an-legend"><i class="sw day"></i>day<i class="sw night"></i>night</span></div>
      <div class="an-hbars">${byHour.map((count,hour)=>{
        const night = hour < DAY_START || hour >= DAY_END;
        return `<div class="an-hb ${night?'night':'day'}" style="--h:${barHeight(count,peak)}%"
          title="${hourLabel(hour)} · ${count} message${count===1?'':'s'}"><span></span></div>`;
      }).join('')}</div>
      <div class="an-axis"><span>12am</span><span>6am</span><span>12pm</span><span>6pm</span><span>11pm</span></div>
    </div>`;
}

function analyticsDayTimeline(byDay){
  if(!byDay.length) return '';
  const peak = Math.max(...byDay.map(d=>d.total), 1);
  // Capped so a long-lived sandbox does not render hundreds of unreadable slivers.
  const shown = byDay.slice(-90);
  return `
    <div class="an-card">
      <div class="an-card-head"><b>Messages per day</b>
        <span class="an-legend"><i class="sw in"></i>from customers<i class="sw out"></i>from business</span></div>
      <div class="an-timeline">${shown.map(day=>`
        <div class="an-tl" title="${esc(shortDate(day.day))} · ${day.total} message${day.total===1?'':'s'} (${day.inbound} in, ${day.outbound} out)">
          <div class="an-tl-stack" style="--h:${barHeight(day.total,peak)}%">
            <span class="an-in" style="flex:${day.inbound||0}"></span>
            <span class="an-out" style="flex:${day.outbound||0}"></span>
          </div></div>`).join('')}</div>
      <div class="an-axis"><span>${esc(shortDate(shown[0].day))}</span><span>${esc(shortDate(shown[shown.length-1].day))}</span></div>
      ${byDay.length > shown.length ? `<div class="an-note">Showing the most recent ${shown.length} of ${byDay.length} days.</div>` : ''}
    </div>`;
}

function analyticsNumberTable(customers, total){
  if(!customers.length) return '';
  const peak = Math.max(...customers.map(c=>c.total), 1);
  return `
    <div class="an-card">
      <div class="an-card-head"><b>Busiest test numbers</b><span class="an-legend">${customers.length} shown</span></div>
      <div class="an-rows">${customers.map(c=>`
        <button type="button" class="an-row" data-open-wa="${esc(c.wa_id)}"
                title="Open ${esc(c.display_name||c.wa_id)} in the phone simulator">
          <span class="an-av" style="background:${esc(c.color||'#25D366')}1f;color:${esc(c.color||'#25D366')}">${esc(initials(c.display_name||c.wa_id))}</span>
          <span class="an-row-main">
            <span class="an-row-name">${esc(c.display_name||c.wa_id)}${c.starred?'<svg class="ico an-star"><use href="#i-star"/></svg>':''}</span>
            <span class="an-row-sub">+${esc(c.wa_id)} · ${c.businesses||0} sender${c.businesses===1?'':'s'}${c.last_at?' · '+esc(new Date(c.last_at).toLocaleString()):''}</span>
          </span>
          <span class="an-split" title="${c.inbound||0} sent by the customer, ${c.outbound||0} sent by the business">
            <i class="an-in" style="width:${total?Math.round((c.inbound||0)/peak*100):0}%"></i>
            <i class="an-out" style="width:${total?Math.round((c.outbound||0)/peak*100):0}%"></i>
          </span>
          <span class="an-row-value">${Number(c.total||0).toLocaleString()}</span>
        </button>`).join('')}</div>
    </div>`;
}

function analyticsSenderTable(senders){
  if(!senders.length) return '';
  const peak = Math.max(...senders.map(s=>s.total), 1);
  return `
    <div class="an-card">
      <div class="an-card-head"><b>By business sender</b></div>
      <div class="an-rows">${senders.map(s=>`
        <div class="an-row static">
          <span class="an-av biz">${esc(initials(s.verified_name))}</span>
          <span class="an-row-main">
            <span class="an-row-name">${esc(s.verified_name)}</span>
            <span class="an-row-sub">+${esc(s.display_phone_number)} · ${s.customers||0} customer${s.customers===1?'':'s'}</span>
          </span>
          <span class="an-meter"><i style="width:${Math.round((s.total||0)/peak*100)}%"></i></span>
          <span class="an-row-value">${Number(s.total||0).toLocaleString()}</span>
        </div>`).join('')}</div>
    </div>`;
}

function analyticsPairMap(pairs){
  if(!pairs.length) return '';
  const peak = Math.max(...pairs.map(p=>p.total), 1);
  return `
    <div class="an-card">
      <div class="an-card-head"><b>Busiest pairings</b>
        <span class="an-legend">which number talks to which sender</span></div>
      <div class="an-rows">${pairs.map(pair=>`
        <button type="button" class="an-row pair" data-open-wa="${esc(pair.wa_id)}" data-open-business="${esc(pair.phone_number_id)}"
                title="Open this conversation in the phone simulator">
          <span class="an-pair">
            <b>${esc(simMobileName(pair.wa_id))}</b>
            <svg class="ico an-arrow"><use href="#i-chevron"/></svg>
            <b>${esc(simBusinessName(pair.phone_number_id))}</b>
          </span>
          <span class="an-meter"><i style="width:${Math.round((pair.total||0)/peak*100)}%"></i></span>
          <span class="an-row-value">${Number(pair.total||0).toLocaleString()}</span>
        </button>`).join('')}</div>
    </div>`;
}

function renderAnalytics(){
  const data = state.analytics;
  for(const prefix of ANALYTICS_TARGETS){
    const box = $('#'+prefix+'-analytics'), range = $('#'+prefix+'-range');
    if(!box) continue;
    if(!data){ box.innerHTML = skeletonRows(3); if(range) range.textContent=''; continue; }
    if(!data.total){
      box.innerHTML = '<div class="empty">No messages yet. Send one and the traffic breakdown appears here.</div>';
      if(range) range.textContent = '';
      continue;
    }
    const peakHour = data.by_hour.indexOf(Math.max(...data.by_hour));
    const nightTotal = data.by_hour.reduce((sum,count,hour)=>
      sum + ((hour < DAY_START || hour >= DAY_END) ? count : 0), 0);
    const activeNumbers = data.customers.filter(c=>c.total>0).length;
    const days = Math.max(1, data.by_day.length);
    if(range){
      const fmt = value => new Date(value).toLocaleDateString([], {month:'short', day:'numeric'});
      range.textContent = fmt(data.first_at) + ' – ' + fmt(data.last_at);
    }
    box.innerHTML = `
      <div class="an-tiles">
        <div class="an-tile"><small>Messages</small><strong>${Number(data.total).toLocaleString()}</strong></div>
        <div class="an-tile"><small>From customers</small><strong>${Number(data.inbound).toLocaleString()}</strong></div>
        <div class="an-tile"><small>From business</small><strong>${Number(data.outbound).toLocaleString()}</strong></div>
        <div class="an-tile"><small>Active numbers</small><strong>${activeNumbers}</strong></div>
        <div class="an-tile"><small>Busiest hour</small><strong>${esc(hourLabel(peakHour))}</strong></div>
        <div class="an-tile"><small>Per active day</small><strong>${(data.total/days).toFixed(1)}</strong></div>
        <div class="an-tile"><small>Overnight</small><strong>${Math.round(nightTotal/data.total*100)}%</strong></div>
      </div>
      <div class="an-grid">
        ${analyticsHourHistogram(data.by_hour)}
        ${analyticsDayTimeline(data.by_day)}
      </div>
      <div class="an-grid">
        ${analyticsNumberTable(data.customers, data.total)}
        ${analyticsSenderTable(data.senders)}
      </div>
      ${analyticsPairMap(data.pairs)}`;
  }
}

let analyticsPending = null;
async function loadAnalytics(force){
  // Coalesced: the dashboard and the simulator page share one payload, and a
  // burst of live messages must not turn into a burst of aggregate queries.
  if(analyticsPending && !force) return analyticsPending;
  // getTimezoneOffset() counts minutes behind UTC; negate for "minutes to add".
  const offset = -new Date().getTimezoneOffset();
  analyticsPending = req(`/_sandbox/analytics?tz_offset=${offset}&top=12`)
    .then(d=>{ state.analytics = d; renderAnalytics(); })
    .catch(()=>{})
    .finally(()=>{ analyticsPending = null; });
  return analyticsPending;
}

/* Live traffic makes the aggregate stale, but re-querying per message would
   cost more than the panel is worth, so refreshes are trailing and throttled. */
let analyticsRefresh = null;
function scheduleAnalyticsRefresh(){
  if(analyticsRefresh) return;
  analyticsRefresh = setTimeout(()=>{ analyticsRefresh = null; loadAnalytics(true); }, 4000);
}

/* Seed the list on first open so it is not empty before anything new arrives. */
function activityEntry(m){
  return {
    id: m.id,
    wa: m.direction === 'inbound' ? m.sender_id : m.recipient_id,
    phoneId: m.direction === 'inbound' ? m.recipient_id : m.sender_id,
    inbound: m.direction === 'inbound',
    type: m.message_type,
    text: simSummary(m),
    template: simTemplateName(m),
    at: m.created_at,
  };
}

async function loadSimActivity(){
  try{
    const d = await req('/_sandbox/messages?limit=' + SIM_FEED_LIMIT);
    state.activity = (d.data||[]).map(activityEntry);
    state.actMore = !!d.has_more;
    state.actBefore = d.next_before || null;
  }catch{ state.activity = []; state.actMore = false; state.actBefore = null; }
  renderSimActivity();
}

/* Older messages for the recent-messages list, on demand. Same keyset cursor
   as the phone transcript, so a message arriving mid-scroll cannot shift the
   page boundaries under the reader. */
async function loadMoreSimActivity(){
  if(!state.actMore || state.actLoading || !state.actBefore) return;
  state.actLoading = true;
  const button = $('#sim-more');
  if(button){ button.disabled = true; button.innerHTML = '<span class="busy-dot"></span> Loading'; }
  try{
    const d = await req('/_sandbox/messages?limit=' + SIM_FEED_LIMIT
      + '&before=' + encodeURIComponent(state.actBefore));
    const seen = new Set(state.activity.map(item=>item.id));
    for(const m of (d.data||[])) if(!seen.has(m.id)) state.activity.push(activityEntry(m));
    state.actMore = !!d.has_more;
    state.actBefore = d.next_before || null;
    state.loadedCap = state.activity.length;
  }catch(e){ toast(e.message, true); }
  finally{ state.actLoading = false; renderSimActivity(); }
}

/* Delegated so the rows keep working across every live re-render. Opens the
   exact pair the row describes - that customer, that business - rather than
   the first business, which is what openPhoneTabFor falls back to. */
document.addEventListener('click', event=>{
  if(event.target.closest('#sim-more')){ loadMoreSimActivity(); return; }
  if(event.target.closest('#hook-more')){ loadMoreWebhooks(); return; }
  // Any row carrying a wa_id opens that conversation, whether it is in the
  // simulator feed, the dashboard feed or the dashboard number list.
  const row = event.target.closest('[data-open-wa]');
  if(!row) return;
  const wa = row.dataset.openWa;
  const biz = row.dataset.openBusiness || state.businesses[0]?.phone_numbers[0]?.id || '';
  window.open(phoneUrl(wa, biz), 'ghost-phone-' + wa);
});

function simLive(live){
  const dash = $('#dash-live');
  if(dash){
    dash.classList.toggle('on', !!live);
    dash.title = live ? 'Live' : 'Reconnecting';
  }
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
      template: simTemplateName(message),
      at: message.created_at || Date.now(),
    });
    // The dashboard message counter and the "Send a message" checklist item
    // both read state.messages, so it has to grow as messages arrive.
    state.messages.unshift(message);
    bumpMessageCount(1);
    scheduleAnalyticsRefresh();
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
