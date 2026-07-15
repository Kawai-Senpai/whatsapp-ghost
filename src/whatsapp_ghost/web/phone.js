/* ===== WhatsApp Web clone — logic ===== */
const params = new URLSearchParams(location.search);
const state = {
  config:{}, businesses:[], users:[],
  wa: params.get('phone') || '',      // the simulated customer (us)
  activePhone: params.get('business') || '',  // business phone_number_id we're chatting with
  socket:null, reconnect:null,
};
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const initials = s => (s||'?').trim().slice(0,2).toUpperCase();
const fmtTime = ts => new Date(ts).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});

async function req(url, options={}){
  const r = await fetch(url, options);
  let d; try{ d = await r.json(); }catch{ d = {error:await r.text()}; }
  if(!r.ok) throw new Error(d?.error?.error_data?.details || d?.error || d?.detail || r.statusText);
  return d;
}

/* Robustly extract a human-readable body from any stored payload shape. */
function messageText(m){
  const p = m.payload || {};
  const t = m.message_type;
  if(t === 'text'){
    // inbound: {text:{body}}  · outbound cloud API: {text:{body}}  · legacy: {body} or string
    let txt = '';
    if(typeof p.text === 'string') txt = p.text;
    else if(p.text && typeof p.text.body === 'string') txt = p.text.body;
    else if(typeof p.body === 'string') txt = p.body;
    return {kind:'text', text:txt};
  }
  if(t === 'template'){
    return {kind:'template', name:(p.template?.name || p.name || 'template')};
  }
  if(['image','video','audio','document','sticker'].includes(t)){
    const media = p[t] || {};
    const label = {image:'Photo',video:'Video',audio:'Audio',document:'Document',sticker:'Sticker'}[t];
    return {kind:'media', mtype:t, src:(media.link||''), caption:(media.caption||media.filename||''), label};
  }
  return {kind:'text', text:'['+t+']'};
}

function ticks(status){
  if(status==='read')      return '<span class="ticks read">✓✓</span>';
  if(status==='delivered') return '<span class="ticks">✓✓</span>';
  if(status==='sent')      return '<span class="ticks">✓</span>';
  if(status==='failed')    return '<span class="ticks" style="color:#e5484d">!</span>';
  return '<span class="ticks" style="letter-spacing:0">⏱</span>';
}

/* ---- boot ---- */
async function boot(){
  try{
    state.config = await req('/_sandbox/config');
    const [biz,users] = await Promise.all([req('/_sandbox/businesses'), req('/_sandbox/phones')]);
    state.businesses = biz.data; state.users = users.data;
    if(!state.wa && state.users.length) state.wa = state.users[0].wa_id;
    const me = state.users.find(u=>u.wa_id===state.wa);
    $('#me-avatar').textContent = initials(me?.display_name || state.wa || 'Y');
    document.title = 'WhatsApp' + (me? ' · '+me.display_name : '');

    renderChatList();
    // auto-open the requested / first business
    const flat = businessPhones();
    if(!state.activePhone && flat.length) state.activePhone = flat[0].id;
    if(state.activePhone) openChat(state.activePhone);
    connectSocket();
  }catch(e){ alert(e.message); }
}

function businessPhones(){
  const out=[];
  state.businesses.forEach(b=>b.phone_numbers.forEach(p=>out.push({...p, business_name:b.name})));
  return out;
}

function renderChatList(){
  const q = ($('#search').value||'').toLowerCase();
  const flat = businessPhones().filter(p=>!q || p.verified_name.toLowerCase().includes(q) || p.display_phone_number.includes(q));
  $('#chat-list').innerHTML = flat.map(p=>`
    <div class="chat-row ${p.id===state.activePhone?'active':''}" onclick="openChat('${esc(p.id)}')">
      <div class="c-avatar">${esc(initials(p.verified_name))}</div>
      <div class="c-main">
        <div class="c-top"><span class="c-name">${esc(p.verified_name)}</span><span class="c-time" id="ct-${esc(p.id)}"></span></div>
        <div class="c-preview" id="cp-${esc(p.id)}">+${esc(p.display_phone_number)}</div>
      </div>
    </div>`).join('') || '<div style="padding:24px;color:var(--muted);text-align:center">No business numbers yet.<br>Add one in the console.</div>';
}

async function openChat(phoneId){
  state.activePhone = phoneId;
  const p = businessPhones().find(x=>x.id===phoneId);
  $('#intro').classList.add('hidden');
  $('#convo').classList.remove('hidden');
  $('#app').classList.add('chat-open');
  $('#convo-name').textContent = p?.verified_name || 'Business';
  $('#convo-avatar').textContent = initials(p?.verified_name);
  $('#convo-status').textContent = p ? '+'+p.display_phone_number : '';
  document.querySelectorAll('.chat-row').forEach(r=>r.classList.remove('active'));
  document.querySelectorAll('.chat-row').forEach(r=>{ if(r.getAttribute('onclick')?.includes(phoneId)) r.classList.add('active'); });
  const url = new URL(location); url.searchParams.set('business', phoneId); url.searchParams.set('phone', state.wa); history.replaceState(null,'',url);
  await loadMessages();
}
function closeChat(){ $('#app').classList.remove('chat-open'); }

async function loadMessages(){
  if(!state.wa){ return; }
  const d = await req('/_sandbox/messages?wa_id='+encodeURIComponent(state.wa)+'&limit=200');
  // messages come newest-first; reverse and keep only ones touching the active business phone
  const all = d.data.reverse().filter(m => m.sender_id===state.activePhone || m.recipient_id===state.activePhone);
  const box = $('#messages');
  if(!all.length){
    box.innerHTML = '<div class="empty-msg">No messages yet.<br>Say hello to open the 24-hour service window.</div>';
    return;
  }
  // last preview + time on chat row
  const last = all[all.length-1];
  const lv = messageText(last);
  const lastPreview = lv.kind==='template' ? 'Template · '+lv.name
    : lv.kind==='media' ? (lv.label + (lv.caption?': '+lv.caption:''))
    : lv.text;
  const cp=$('#cp-'+CSS.escape(state.activePhone)), ct=$('#ct-'+CSS.escape(state.activePhone));
  if(cp) cp.textContent = lastPreview.slice(0,42); if(ct) ct.textContent = fmtTime(last.created_at);

  let lastDay = '';
  box.innerHTML = all.map(m=>{
    // inbound = FROM customer (us) → show on right ("out"); outbound = from business → left ("in")
    const mine = m.direction === 'inbound';
    const val = messageText(m);
    const day = new Date(m.created_at).toLocaleDateString([], {weekday:'long', month:'short', day:'numeric'});
    let sep=''; if(day!==lastDay){ lastDay=day; sep=`<div class="day-sep">${esc(day)}</div>`; }
    let bodyHtml = '';
    if(val.kind==='template'){
      bodyHtml = `<span class="tpl-tag">TEMPLATE</span><span class="body">${esc(val.name)}</span>`;
    } else if(val.kind==='media'){
      if(val.mtype==='image' && val.src) bodyHtml += `<img class="media-thumb" src="${esc(val.src)}" alt="">`;
      else bodyHtml += `<span class="tpl-tag">${esc(val.label).toUpperCase()}</span>`;
      if(val.caption) bodyHtml += `<span class="body">${esc(val.caption)}</span>`;
    } else {
      bodyHtml = `<span class="body">${esc(val.text)}</span>`;
    }
    const meta = `<span class="meta">${fmtTime(m.created_at)}${mine?' '+ticks(m.status):''}</span>`;
    return `${sep}<div class="msg ${mine?'out':'in'} ${val.kind==='template'?'tpl':''}" onclick="this.classList.toggle('show-raw')">
      ${bodyHtml}${meta}
      <div class="raw">${esc(JSON.stringify(m.payload,null,2))}</div>
    </div>`;
  }).join('');
  box.scrollTop = box.scrollHeight;
}

/* ---- send as the customer ---- */
async function sendInbound(body){
  if(!state.wa || !state.activePhone){ return; }
  await req(`/_sandbox/phones/${encodeURIComponent(state.wa)}/messages`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({...body, phone_number_id: state.activePhone})
  });
  await loadMessages();
}
$('#send-form').addEventListener('submit', async e=>{
  e.preventDefault();
  const input = $('#msg-input'), text = input.value.trim();
  if(!text){ return; }
  input.value='';
  try{ await sendInbound({type:'text', text}); }catch(x){ alert(x.message); }
});

/* ---- attach an image (stored as a data URL link) ---- */
$('#file-input').addEventListener('change', async e=>{
  const file = e.target.files[0]; e.target.value='';
  if(!file) return;
  if(file.size > 1_500_000){ alert('Please pick an image under ~1.5 MB for the local sandbox.'); return; }
  const dataUrl = await new Promise((res,rej)=>{ const r=new FileReader(); r.onload=()=>res(r.result); r.onerror=rej; r.readAsDataURL(file); });
  const caption = $('#msg-input').value.trim(); $('#msg-input').value='';
  try{ await sendInbound({type:'image', image:{link:dataUrl, caption, filename:file.name}}); }
  catch(x){ alert(x.message); }
});

/* ---- live updates ---- */
function connectSocket(){
  if(state.socket){ state.socket.onclose=null; state.socket.close(); }
  if(state.reconnect) clearTimeout(state.reconnect);
  if(!state.wa) return;
  const scheme = location.protocol==='https:'?'wss':'ws';
  const wa = state.wa;
  state.socket = new WebSocket(`${scheme}://${location.host}/_sandbox/clients/${encodeURIComponent(wa)}`);
  state.socket.onmessage = ()=>{ if(state.wa===wa) loadMessages(); };
  state.socket.onclose = ()=>{ if(state.wa===wa) state.reconnect=setTimeout(connectSocket,1500); };
}

$('#search').addEventListener('input', renderChatList);

// keep business/customer names in sync with edits made in the console
async function refreshMeta(){
  try{
    const [biz,users] = await Promise.all([req('/_sandbox/businesses'), req('/_sandbox/phones')]);
    state.businesses = biz.data; state.users = users.data;
    const me = state.users.find(u=>u.wa_id===state.wa);
    $('#me-avatar').textContent = initials(me?.display_name || state.wa || 'Y');
    renderChatList();
    if(state.activePhone){
      const p = businessPhones().find(x=>x.id===state.activePhone);
      if(p){ $('#convo-name').textContent = p.verified_name; $('#convo-avatar').textContent = initials(p.verified_name); $('#convo-status').textContent = '+'+p.display_phone_number; }
    }
  }catch{}
}

// poll as a fallback so status ticks + renamed businesses refresh even without a socket event
setInterval(()=>{ if(state.activePhone) loadMessages(); }, 4000);
setInterval(refreshMeta, 5000);
boot();
