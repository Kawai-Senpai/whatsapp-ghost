/* ===== WhatsApp Web clone — logic ===== */
const params = new URLSearchParams(location.search);
const state = {
  config:{}, businesses:[], users:[], templates:{},
  wa: params.get('phone') || '',      // the simulated customer (us)
  activePhone: params.get('business') || '',  // business phone_number_id we're chatting with
  socket:null, reconnect:null, reading:false, messages:new Map(), replying:null, loadSequence:0,
  pinned:new Set(),  // server-backed; loaded per customer from /_sandbox/phones/{wa}/pins
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

/* Render a template's body text with its positional {{n}} parameters filled
   in, mirroring what the recipient actually sees in WhatsApp. Falls back to
   an empty string when the template definition has not been loaded. */
function templateParamValue(param){
  if(!param || typeof param !== 'object') return '';
  if(param.type === 'currency') return param.currency?.fallback_value ?? '';
  if(param.type === 'date_time') return param.date_time?.fallback_value ?? '';
  return param.text ?? '';
}

function renderTemplateBody(name, tpl){
  const key = name + '|' + (tpl.language?.code || '');
  const definition = state.templates[key] || state.templates[name];
  if(!definition) return '';
  const bodyComponent = (definition.components || []).find(c => (c.type||'').toUpperCase() === 'BODY');
  if(!bodyComponent || typeof bodyComponent.text !== 'string') return '';
  const sent = (tpl.components || []).find(c => (c.type||'').toLowerCase() === 'body');
  const values = (sent?.parameters || []).map(templateParamValue);
  return bodyComponent.text.replace(/\{\{(\d+)\}\}/g, (match, index) => {
    const value = values[Number(index) - 1];
    return value === undefined || value === '' ? match : value;
  });
}

function sentButtonParameter(tpl, index){
  const sent = (tpl.components || []).find(c =>
    (c.type||'').toLowerCase() === 'button' && Number(c.index) === index);
  return sent?.parameters?.[0] || {};
}

function renderTemplateButtons(name, tpl){
  const key = name + '|' + (tpl.language?.code || '');
  const definition = state.templates[key] || state.templates[name];
  const container = (definition?.components || []).find(c => (c.type||'').toUpperCase() === 'BUTTONS');
  return (container?.buttons || []).map((button,index)=>{
    const type=(button.type||'').toUpperCase(), label=button.text || (type==='OTP'?'Copy code':type);
    const parameter=sentButtonParameter(tpl,index);
    if(type==='URL'){
      const suffix=parameter.text ?? parameter.payload ?? '';
      const href=String(button.url||'').replace(/\{\{1\}\}/g,encodeURIComponent(suffix));
      if(!/^https?:\/\//i.test(href)) return '';
      return `<a class="tpl-button" href="${esc(href)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">↗ ${esc(label)}</a>`;
    }
    if(type==='PHONE_NUMBER'){
      const phone=String(button.phone_number||'').replace(/[^+\d]/g,'');
      return `<a class="tpl-button" href="tel:${esc(phone)}" onclick="event.stopPropagation()">☎ ${esc(label)}</a>`;
    }
    const payload=parameter.payload || parameter.text || label;
    return `<button type="button" class="tpl-button" data-template-reply="${esc(payload)}" data-template-label="${esc(label)}">${esc(label)}</button>`;
  }).filter(Boolean);
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
    const tpl = p.template || {};
    const name = tpl.name || p.name || 'template';
    // A real client shows the rendered body, not the template name, so the
    // positional {{n}} values are substituted from the sent parameters.
    return {kind:'template', name, text: renderTemplateBody(name, tpl), buttons:renderTemplateButtons(name,tpl)};
  }
  if(t === 'button') return {kind:'text', text:p.button?.text || p.button?.payload || 'Button reply'};
  if(['image','video','audio','document','sticker'].includes(t)){
    const media = p[t] || {};
    const label = {image:'Photo',video:'Video',audio:'Audio',document:'Document',sticker:'Sticker'}[t];
    const stored = media.id && state.config.access_token
      ? `/_sandbox/media/${encodeURIComponent(media.id)}?access_token=${encodeURIComponent(state.config.access_token)}` : '';
    return {kind:'media', mtype:t, src:(media.link||stored), caption:(media.caption||media.filename||''), label};
  }
  return {kind:'text', text:'['+t+']'};
}

function ticks(status){
  if(status==='read')      return '<span class="ticks read" title="Read">&#10003;&#10003;</span>';
  if(status==='delivered') return '<span class="ticks" title="Delivered">&#10003;&#10003;</span>';
  if(status==='sent')      return '<span class="ticks" title="Sent">&#10003;</span>';
  if(status==='failed')    return '<span class="ticks" style="color:#e5484d">!</span>';
  return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
}

function jsonHtml(value){
  const safe = esc(JSON.stringify(value,null,2));
  return safe.replace(/(&quot;(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\&])*&quot;)(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?/g, match=>{
    let cls='json-number';
    if(/^&quot;/.test(match)) cls=/:$/.test(match)?'json-key':'json-string';
    else if(/true|false/.test(match)) cls='json-boolean';
    else if(/null/.test(match)) cls='json-null';
    return `<span class="${cls}">${match}</span>`;
  });
}

/* Template bodies live on the WABA, so they are fetched once per business and
   keyed by name|language for rendering sent template messages. */
async function loadTemplateDefinitions(){
  const token = state.config.access_token;
  if(!token) return;
  for(const business of state.businesses){
    try{
      const d = await req(`/v25.0/${business.id}/message_templates`, {headers:{Authorization:'Bearer '+token}});
      for(const tpl of (d.data || [])){
        state.templates[tpl.name + '|' + tpl.language] = tpl;
        state.templates[tpl.name] = tpl;
      }
    }catch{ /* a business without readable templates just renders the name */ }
  }
}

/* ---- boot ---- */
async function boot(){
  try{
    state.config = await req('/_sandbox/config');
    const [biz,users] = await Promise.all([req('/_sandbox/businesses'), req('/_sandbox/phones')]);
    state.businesses = biz.data; state.users = users.data;
    await loadTemplateDefinitions();
    if(!state.wa && state.users.length) state.wa = state.users[0].wa_id;
    const me = state.users.find(u=>u.wa_id===state.wa);
    $('#me-avatar').textContent = initials(me?.display_name || state.wa || 'Y');
    document.title = 'WhatsApp' + (me? ' · '+me.display_name : '');

    await loadPins();
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
  const flat = businessPhones().filter(p=>!q || p.verified_name.toLowerCase().includes(q) || p.display_phone_number.includes(q))
    .sort((a,b)=>Number(state.pinned.has(b.id))-Number(state.pinned.has(a.id)));
  $('#chat-list').innerHTML = flat.map(p=>`
    <div class="chat-row ${p.id===state.activePhone?'active':''}" onclick="openChat('${esc(p.id)}')">
      <div class="c-avatar">${esc(initials(p.verified_name))}</div>
      <div class="c-main">
        <div class="c-top"><span class="c-name">${esc(p.verified_name)}</span><span class="c-row-meta">${state.pinned.has(p.id)?'<span class="c-pin" title="Pinned">📌</span>':''}<span class="c-time" id="ct-${esc(p.id)}"></span></span></div>
        <div class="c-preview" id="cp-${esc(p.id)}">+${esc(p.display_phone_number)}</div>
      </div>
    </div>`).join('') || '<div style="padding:24px;color:var(--muted);text-align:center">No business numbers yet.<br>Add one in the console.</div>';
}

async function loadPins(){
  if(!state.wa){ state.pinned=new Set(); return; }
  try{
    const d = await req('/_sandbox/phones/'+encodeURIComponent(state.wa)+'/pins');
    state.pinned = new Set(d.data||[]);
  }catch{ state.pinned = new Set(); }
}

async function togglePin(phoneId){
  const next = !state.pinned.has(phoneId);
  if(next) state.pinned.add(phoneId); else state.pinned.delete(phoneId);
  if(phoneId===state.activePhone) $('#menu-pin-chat').textContent=next?'Unpin chat':'Pin chat';
  try{
    await req('/_sandbox/phones/'+encodeURIComponent(state.wa)+'/pins',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({phone_number_id:phoneId,pinned:next})
    });
  }catch(e){
    // Roll back so the sidebar never disagrees with what was stored.
    if(next) state.pinned.delete(phoneId); else state.pinned.add(phoneId);
    if(phoneId===state.activePhone) $('#menu-pin-chat').textContent=state.pinned.has(phoneId)?'Unpin chat':'Pin chat';
  }
  renderChatList();
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
  $('#menu-pin-chat').textContent=state.pinned.has(phoneId)?'Unpin chat':'Pin chat';
  document.querySelectorAll('.chat-row').forEach(r=>r.classList.remove('active'));
  document.querySelectorAll('.chat-row').forEach(r=>{ if(r.getAttribute('onclick')?.includes(phoneId)) r.classList.add('active'); });
  const url = new URL(location); url.searchParams.set('business', phoneId); url.searchParams.set('phone', state.wa); history.replaceState(null,'',url);
  await loadMessages();
}
function closeChat(){ $('#app').classList.remove('chat-open'); }

async function loadMessages(){
  if(!state.wa){ return; }
  const sequence=++state.loadSequence;
  const d = await req('/_sandbox/messages?wa_id='+encodeURIComponent(state.wa)+'&phone_number_id='+encodeURIComponent(state.activePhone)+'&limit=200');
  if(sequence!==state.loadSequence) return;
  const all = d.data.reverse();
  state.messages = new Map(all.map(message=>[message.id,message]));
  const unread = all.filter(m=>m.direction==='outbound' && ['accepted','sent','delivered'].includes(m.status));
  if(unread.length && !state.reading){
    state.reading=true;
    try{
      await req(`/_sandbox/phones/${encodeURIComponent(state.wa)}/read`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone_number_id:state.activePhone})});
      unread.forEach(m=>m.status='read');
    }finally{ state.reading=false; }
  }
  const box = $('#messages');
  if(!all.length){
    box.innerHTML = '<div class="empty-msg">No messages yet.<br>Say hello to open the 24-hour service window.</div>';
    return;
  }
  // last preview + time on chat row
  const visible = all.filter(message=>message.message_type!=='reaction');
  const last = visible[visible.length-1] || all[all.length-1];
  const lv = messageText(last);
  const lastPreview = lv.kind==='template' ? (lv.text || 'Template · '+lv.name)
    : lv.kind==='media' ? (lv.label + (lv.caption?': '+lv.caption:''))
    : lv.text;
  const cp=$('#cp-'+CSS.escape(state.activePhone)), ct=$('#ct-'+CSS.escape(state.activePhone));
  if(cp) cp.textContent = lastPreview.slice(0,42); if(ct) ct.textContent = fmtTime(last.created_at);

  const query=($('#convo-search-input')?.value||'').trim().toLowerCase();
  const reactions = new Map();
  all.filter(message=>message.message_type==='reaction').forEach(message=>{
    const reaction=message.payload?.reaction || {};
    if(!reaction.message_id || !reaction.emoji) return;
    const values=reactions.get(reaction.message_id)||[]; values.push(reaction.emoji); reactions.set(reaction.message_id,values);
  });
  let lastDay = '';
  box.innerHTML = visible.map(m=>{
    // inbound = FROM customer (us) → show on right ("out"); outbound = from business → left ("in")
    const mine = m.direction === 'inbound';
    const val = messageText(m);
    const searchable=(val.text||val.caption||val.name||'').toLowerCase();
    if(query && !searchable.includes(query)) return '';
    const day = new Date(m.created_at).toLocaleDateString([], {weekday:'long', month:'short', day:'numeric'});
    let sep=''; if(day!==lastDay){ lastDay=day; sep=`<div class="day-sep">${esc(day)}</div>`; }
    let bodyHtml = '';
    const contextId=m.payload?.context?.id;
    const quoted=contextId?state.messages.get(contextId):null;
    if(quoted){const quote=messageText(quoted);bodyHtml+=`<div class="reply-quote"><b>${quoted.direction==='inbound'?'You':'Business'}</b><span>${esc(quote.text||quote.caption||quote.name||'Message')}</span></div>`;}
    if(val.kind==='template'){
      const buttons=(val.buttons||[]).length?`<div class="tpl-buttons">${val.buttons.join('')}</div>`:'';
      bodyHtml = `<span class="tpl-tag">TEMPLATE</span><span class="body">${esc(val.text || val.name)}</span>${buttons}`;
    } else if(val.kind==='media'){
      if(val.mtype==='image' && val.src) bodyHtml += `<img class="media-thumb" src="${esc(val.src)}" alt="">`;
      else bodyHtml += `<span class="tpl-tag">${esc(val.label).toUpperCase()}</span>`;
      if(val.caption) bodyHtml += `<span class="body">${esc(val.caption)}</span>`;
    } else {
      bodyHtml += `<span class="body">${esc(val.text)}</span>`;
    }
    const meta = `<span class="meta">${fmtTime(m.created_at)}${mine?' '+ticks(m.status):''}</span>`;
    const reactionHtml=(reactions.get(m.id)||[]).length?`<div class="reaction-badge">${esc((reactions.get(m.id)||[]).join(' '))}</div>`:'';
    const actions=`<button type="button" class="msg-action-toggle" data-message-actions="${esc(m.id)}" title="Message actions" aria-label="Message actions">⌄</button><div class="msg-action-menu"><button type="button" class="reply-action" data-reply-id="${esc(m.id)}">↩ Reply</button><div class="reaction-choices">${['👍','❤️','😂','😮','😢','🙏'].map(emoji=>`<button type="button" data-react-id="${esc(m.id)}" data-emoji="${emoji}" title="React ${emoji}">${emoji}</button>`).join('')}</div></div>`;
    return `${sep}<div class="msg ${mine?'out':'in'} ${val.kind==='template'?'tpl':''}" data-message-id="${esc(m.id)}" onclick="this.classList.toggle('show-raw')">
      ${actions}
      ${bodyHtml}${meta}
      ${reactionHtml}
      <div class="raw json-view">${jsonHtml(m.payload)}</div>
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
$('#messages').addEventListener('click', async event=>{
  const button=event.target.closest('[data-template-reply]');
  if(!button) return;
  event.stopPropagation();
  button.disabled=true;
  try{
    await sendInbound({type:'button',button:{payload:button.dataset.templateReply,text:button.dataset.templateLabel}});
  }catch(error){
    alert(error.message);
    button.disabled=false;
  }
});
$('#messages').addEventListener('click', async event=>{
  const toggle=event.target.closest('[data-message-actions]');
  if(toggle){
    event.stopPropagation();
    const message=toggle.closest('.msg');
    const opening=!message.classList.contains('actions-open');
    document.querySelectorAll('.msg.actions-open').forEach(item=>item.classList.remove('actions-open'));
    message.classList.toggle('actions-open',opening);
    return;
  }
  const reply=event.target.closest('[data-reply-id]');
  if(reply){event.stopPropagation();startReply(reply.dataset.replyId);return;}
  const reaction=event.target.closest('[data-react-id]');
  if(reaction){
    event.stopPropagation();
    await sendInbound({type:'reaction',reaction:{message_id:reaction.dataset.reactId,emoji:reaction.dataset.emoji}});
  }
});
function startReply(messageId){
  const message=state.messages.get(messageId); if(!message) return;
  const value=messageText(message);
  state.replying=messageId;
  $('#reply-text').textContent=value.text||value.caption||value.name||'Message';
  $('#reply-composer').classList.remove('hidden');
  $('#msg-input').focus();
}
function cancelReply(){state.replying=null;$('#reply-composer').classList.add('hidden');$('#reply-text').textContent='';}
$('#cancel-reply').addEventListener('click',cancelReply);
$('#send-form').addEventListener('submit', async e=>{
  e.preventDefault();
  const input = $('#msg-input'), text = input.value.trim();
  if(!text){ return; }
  input.value='';
  try{ await sendInbound({type:'text', text, ...(state.replying?{context:{id:state.replying}}:{})}); cancelReply(); }catch(x){ alert(x.message); }
});

const emojiChoices=['😀','😂','😍','👍','🙏','🎉','❤️','😢','😮','🔥','✅','👋'];
$('#emoji-picker').innerHTML=emojiChoices.map(emoji=>`<button type="button" data-compose-emoji="${emoji}">${emoji}</button>`).join('');
$('#emoji-btn').addEventListener('click',()=>$('#emoji-picker').classList.toggle('hidden'));
$('#emoji-picker').addEventListener('click',event=>{
  const button=event.target.closest('[data-compose-emoji]'); if(!button)return;
  const input=$('#msg-input'),start=input.selectionStart??input.value.length,end=input.selectionEnd??start;
  input.value=input.value.slice(0,start)+button.dataset.composeEmoji+input.value.slice(end);
  input.focus(); input.setSelectionRange(start+button.dataset.composeEmoji.length,start+button.dataset.composeEmoji.length);
});
$('#new-chat-btn').addEventListener('click',()=>{$('#search').focus();$('#search').select();});
$('#pane-menu-btn').addEventListener('click',()=>{$('#search').focus();});
$('#convo-search-btn').addEventListener('click',()=>{$('#convo-search').classList.toggle('hidden');$('#convo-search-input').focus();});
$('#close-convo-search').addEventListener('click',()=>{$('#convo-search-input').value='';$('#convo-search').classList.add('hidden');loadMessages();});
$('#convo-search-input').addEventListener('input',loadMessages);
$('#convo-menu-btn').addEventListener('click',event=>{event.stopPropagation();$('#convo-menu').classList.toggle('hidden');});
$('#menu-pin-chat').addEventListener('click',async ()=>{$('#convo-menu').classList.add('hidden');await togglePin(state.activePhone);renderChatList();});
$('#menu-contact-info').addEventListener('click',()=>{const phone=businessPhones().find(item=>item.id===state.activePhone);alert(phone?`${phone.verified_name}\n+${phone.display_phone_number}\n${phone.business_name}`:'Contact unavailable');});
document.addEventListener('click',event=>{if(!event.target.closest('#convo-menu')&&!event.target.closest('#convo-menu-btn'))$('#convo-menu').classList.add('hidden');});
document.addEventListener('click',event=>{if(!event.target.closest('.msg-action-menu')&&!event.target.closest('.msg-action-toggle'))document.querySelectorAll('.msg.actions-open').forEach(item=>item.classList.remove('actions-open'));});

/* ---- attach an image through the same media-ID flow as Cloud API ---- */
$('#file-input').addEventListener('change', async e=>{
  const file = e.target.files[0]; e.target.value='';
  if(!file) return;
  if(file.size > 5_000_000){ alert('Please pick an image under 5 MB.'); return; }
  const caption = $('#msg-input').value.trim(); $('#msg-input').value='';
  try{
    const form = new FormData(); form.append('messaging_product','whatsapp'); form.append('file',file,file.name);
    const auth = {Authorization:'Bearer '+state.config.access_token};
    const uploaded = await req(`/v25.0/${encodeURIComponent(state.activePhone)}/media`,{method:'POST',headers:auth,body:form});
    const metadata = await req(`/v25.0/${encodeURIComponent(uploaded.id)}`,{headers:auth});
    await sendInbound({type:'image', image:{id:uploaded.id,mime_type:metadata.mime_type,sha256:metadata.sha256,caption}});
  }
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

window.addEventListener('focus', refreshMeta);
boot();
