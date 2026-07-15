/* ===== WhatsApp Ghost developer console ===== */
const state = { config:{}, apps:[], businesses:[], users:[], messages:[] };
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const initials = s => (s||'?').trim().slice(0,2).toUpperCase();

async function req(url, options={}) {
  const r = await fetch(url, options);
  let data; try { data = await r.json(); } catch { data = { error: await r.text() }; }
  if (!r.ok) throw new Error(data?.error?.error_data?.details || data?.error || data?.detail || r.statusText);
  return data;
}
function toast(msg, bad=false){ const e=$('#toast'); e.textContent=msg; e.classList.toggle('bad',bad); e.classList.add('show'); clearTimeout(e._t); e._t=setTimeout(()=>e.classList.remove('show'),2600); }
function copyText(v){ navigator.clipboard.writeText(v); toast('Copied to clipboard'); }
function openModal(id){ $('#'+id).classList.add('open'); }
function closeModal(id){ $('#'+id).classList.remove('open'); }

/* ---- navigation ---- */
function goto(page){
  document.querySelectorAll('.side-link').forEach(b=>b.classList.toggle('active', b.dataset.page===page));
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active', p.id===page));
  window.scrollTo(0,0);
  if(page==='webhooks') loadWebhooks();
  if(page==='templates') loadTemplates();
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
    $('#mode-foot').textContent = state.config.mode.toUpperCase();
    $('#endpoint').textContent = state.config.base_url + '/v25.0/PHONE_LOCAL/messages';
    $('#m-apps').textContent = state.apps.length;
    $('#m-numbers').textContent = numbers;
    $('#m-users').textContent = state.users.length;
    $('#m-messages').textContent = state.messages.length;

    setTask('task-number', numbers>0); setTask('s-number', numbers>0);
    setTask('task-user', state.users.length>0); setTask('s-user', state.users.length>0);
    setTask('task-msg', state.messages.length>0); setTask('s-msg', state.messages.length>0);

    renderApps(); renderBusinesses(); renderUsers(); fillSelectors();
    await loadTemplates(); await loadWebhooks();
  }catch(e){ toast(e.message,true); }
}
function setTask(id, done){ const el=$('#'+id); if(el) el.classList.toggle('done', done); const c=el?.querySelector('.check'); if(c) c.textContent=done?'✓':''; }

/* ---- render: apps ---- */
function renderApps(){
  $('#app-id-side').textContent = state.apps[0]?.id || 'local';
  $('#app-list').innerHTML = state.apps.map(a=>`
    <div class="item">
      <div class="item-head">
        <div class="avatar">🔑</div>
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
        <div class="grow"><b>${esc(b.name)} <span class="badge blue">WABA</span></b>
          <small>Business ${esc(b.business_id)} · ${esc(b.id)}</small></div>
      </div>
      ${b.phone_numbers.map(p=>`
        <div class="subnumber">
          <div class="avatar wa" style="width:34px;height:34px;border-radius:9px">☎</div>
          <div class="grow"><b>${esc(p.verified_name)} <span class="badge">${esc(p.quality_rating||'GREEN')}</span></b>
            <small>+${esc(p.display_phone_number)} · ${esc(p.id)}</small></div>
          <button class="btn secondary small" onclick='editBusiness(${JSON.stringify(b.id)},${JSON.stringify(b.name)},${JSON.stringify(p.id)},${JSON.stringify(p.verified_name)},${JSON.stringify(p.display_phone_number)})'>✎ Edit</button>
        </div>`).join('')}
    </div>`).join('') || '<div class="empty">No businesses yet. Add one to register a sender number.</div>';
}

/* ---- render: test users ---- */
function renderUsers(){
  $('#user-list').innerHTML = state.users.map(u=>`
    <div class="item"><div class="item-head">
      <div class="avatar wa">${esc(initials(u.display_name))}</div>
      <div class="grow"><b>${esc(u.display_name)}</b><small>+${esc(u.wa_id)}</small></div>
      <button class="btn wa small" onclick="openPhoneTabFor('${esc(u.wa_id)}')"><svg><use href="#i-open"/></svg>Open</button>
      <button class="btn danger small" title="Delete customer" onclick="deletePhone('${esc(u.wa_id)}','${esc(u.display_name)}')">Delete</button>
    </div></div>`).join('') || '<div class="empty">No test customers yet.</div>';
}

/* ---- selectors ---- */
function fillSelectors(){
  const wabaOpts = state.businesses.map(b=>`<option value="${esc(b.id)}">${esc(b.name)} · ${esc(b.id)}</option>`).join('');
  $('#tpl-waba').innerHTML = wabaOpts; $('#wh-waba').innerHTML = wabaOpts;
  $('#sim-user').innerHTML = state.users.map(u=>`<option value="${esc(u.wa_id)}">${esc(u.display_name)} · +${esc(u.wa_id)}</option>`).join('')
    || '<option value="">— add a customer first —</option>';
  const bizNums=[]; state.businesses.forEach(b=>b.phone_numbers.forEach(p=>bizNums.push(`<option value="${esc(p.id)}">${esc(p.verified_name)} · +${esc(p.display_phone_number)}</option>`)));
  $('#sim-business').innerHTML = bizNums.join('') || '<option value="">— add a business first —</option>';
}

/* ---- templates / webhooks ---- */
async function loadTemplates(){
  if(!state.config.access_token) return;
  let all=[];
  for(const b of state.businesses){
    try{ const d=await req(`/v25.0/${b.id}/message_templates`,{headers:{Authorization:'Bearer '+state.config.access_token}}); all.push(...d.data.map(t=>({...t,_waba:b.name}))); }catch{}
  }
  $('#template-list').innerHTML = all.map(t=>`
    <div class="item"><div class="item-head">
      <div class="avatar">▤</div>
      <div class="grow"><b>${esc(t.name)} <span class="badge">${esc(t.status)}</span></b>
        <small>${esc(t.language)} · ${esc(t.category)} · ${esc(t._waba)}</small></div></div>
      <div style="margin-top:10px;color:var(--muted)">${esc(t.components?.find(c=>c.type==='BODY')?.text||'')}</div>
    </div>`).join('') || '<div class="empty">No templates yet.</div>';
}
async function loadWebhooks(){
  const d = await req('/_sandbox/webhooks');
  $('#webhook-list').innerHTML = d.data.map(w=>`
    <div class="item"><div class="item-head">
      <div class="avatar">↯</div>
      <div class="grow"><b>${esc(w.event_type)} <span class="badge ${w.status==='delivered'?'':'amber'}">${esc(w.status)}</span></b>
        <small>${esc(w.destination_url||'No subscription configured')} · ${esc(w.id)}</small></div>
      ${w.destination_url?`<button class="btn secondary small" onclick="replay('${esc(w.id)}')">Replay</button>`:''}
    </div>
      <details style="margin-top:10px"><summary style="cursor:pointer;color:var(--muted)">Raw payload &amp; signature</summary>
        <div class="endpoint" style="margin-top:8px">${esc(JSON.stringify(w.request_body,null,2))}</div>
        <div class="secret"><code>${esc(w.signature)}</code></div></details>
    </div>`).join('') || '<div class="empty">Events appear here as messages and statuses are created.</div>';
}

/* ---- actions ---- */
async function createApp(e){ e.preventDefault();
  try{ await req('/_sandbox/apps',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#app-name').value})});
    closeModal('app-modal'); e.target.reset(); toast('App and credentials created'); loadAll(); }catch(x){ toast(x.message,true); } }
async function rotateToken(id){ try{ const d=await req(`/_sandbox/apps/${id}/rotate-token`,{method:'POST'}); copyText(d.access_token); toast('Token rotated and copied'); loadAll(); }catch(e){ toast(e.message,true); } }
async function createBusiness(e){ e.preventDefault();
  try{ await req('/_sandbox/businesses',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:$('#biz-name').value,verified_name:$('#biz-verified').value,display_phone_number:$('#biz-phone').value})});
    closeModal('business-modal'); e.target.reset(); toast('Business, WABA and sender created'); loadAll(); }catch(x){ toast(x.message,true); } }

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
    await req(`/v25.0/${w}/message_templates`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+state.config.access_token},
      body:JSON.stringify({name:$('#tpl-name').value,language:$('#tpl-language').value,category:$('#tpl-category').value,components:[{type:'BODY',text:$('#tpl-body').value}]})});
    closeModal('template-modal'); e.target.reset(); toast('Template approved locally'); loadTemplates(); }catch(x){ toast(x.message,true); } }
async function createWebhook(e){ e.preventDefault();
  try{ await req(`/v25.0/${$('#wh-waba').value}/subscribed_apps`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+state.config.access_token},
      body:JSON.stringify({callback_url:$('#wh-url').value,verify_token:$('#wh-verify').value||undefined})});
    closeModal('webhook-modal'); e.target.reset(); toast('Webhook verified and subscribed'); loadWebhooks(); }catch(x){ toast(x.message,true); } }
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

/* ---- modal backdrop close + boot ---- */
document.querySelectorAll('.modal-back').forEach(m=>m.addEventListener('click',e=>{ if(e.target===m) m.classList.remove('open'); }));
if(location.hash==='#simulator') goto('simulator');
loadAll();
