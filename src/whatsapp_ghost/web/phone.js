/* ===== WhatsApp Web clone — logic ===== */
const params = new URLSearchParams(location.search);
const state = {
  config:{}, businesses:[], users:[], templates:{},
  wa: params.get('phone') || '',      // the simulated customer (us)
  activePhone: params.get('business') || '',  // business phone_number_id we're chatting with
  socket:null, reconnect:null, reading:false, messages:new Map(), replying:null, loadSequence:0,
  pinned:new Set(),  // server-backed; loaded per customer from /_sandbox/phones/{wa}/pins
  hasMore:false, nextBefore:null, loadingEarlier:false,  // message pagination cursor
  observer:null, observerRetry:null,   // firehose socket: every mobile, not just ours
  feed:[],                              // newest-first arrivals shown in the live inbox
  unread:new Map(),                     // wa_id -> unread count, derived from message status
  chatUnread:new Map(),                 // wa_id|phone_number_id -> {unread, at}
  chatActivity:new Map(),               // phone_number_id -> last message time, for ordering
};
const FEED_LIMIT = 60;
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
    applyMeAvatar(me);
    document.title = 'WhatsApp' + (me? ' · '+me.display_name : '');

    await loadPins();
    renderChatList();
    // auto-open the requested / first business
    const flat = businessPhones();
    if(!state.activePhone && flat.length) state.activePhone = flat[0].id;
    if(state.activePhone) openChat(state.activePhone);
    connectSocket();
    await loadUnread();
    renderMobiles();
    renderChatList();
    renderFeed();
    connectObserver();
  }catch(e){ alert(e.message); }
}

/* The simulated customer carries a colour, chosen in the console and shown on
   their row in the live inbox. Using it here too means the avatar in this tab
   matches the one you clicked to get here, instead of a generic grey. */
function applyMeAvatar(me){
  const avatar = $('#me-avatar');
  if(!avatar) return;
  avatar.textContent = initials(me?.display_name || state.wa || 'Y');
  if(me?.color){ avatar.style.background = me.color; avatar.style.color = '#fff'; }
}

function businessPhones(){
  const out=[];
  state.businesses.forEach(b=>b.phone_numbers.forEach(p=>out.push({...p, business_name:b.name})));
  return out;
}

function chatKey(phoneId){ return state.wa+'|'+phoneId; }
function chatUnread(phoneId){ return state.chatUnread.get(chatKey(phoneId))?.unread || 0; }
function chatLastAt(phoneId){
  const at = state.chatActivity.get(chatKey(phoneId));
  return at ? new Date(at).getTime() : 0;
}

function renderChatList(){
  const q = ($('#search').value||'').toLowerCase();
  // Pinned first, then most recently active, the way a real client orders
  // chats. Before this the order was whatever businessPhones() happened to
  // return, so a chat that just received something did not move.
  const flat = businessPhones().filter(p=>!q || p.verified_name.toLowerCase().includes(q) || p.display_phone_number.includes(q))
    .sort((a,b)=>
      Number(state.pinned.has(b.id))-Number(state.pinned.has(a.id))
      || chatLastAt(b.id)-chatLastAt(a.id)
      || a.verified_name.localeCompare(b.verified_name));
  $('#chat-list').innerHTML = flat.map(p=>{
    const count = chatUnread(p.id);
    const at = chatLastAt(p.id);
    return `
    <div class="chat-row ${p.id===state.activePhone?'active':''} ${count?'has-unread':''}" onclick="openChat('${esc(p.id)}')">
      <div class="c-avatar">${esc(initials(p.verified_name))}</div>
      <div class="c-main">
        <div class="c-top"><span class="c-name">${esc(p.verified_name)}</span><span class="c-row-meta">${state.pinned.has(p.id)?'<span class="c-pin" title="Pinned">📌</span>':''}<span class="c-time" id="ct-${esc(p.id)}">${at?esc(fmtTime(at)):''}</span></span></div>
        <div class="c-bottom"><div class="c-preview" id="cp-${esc(p.id)}">+${esc(p.display_phone_number)}</div>${count?`<span class="c-unread">${count>99?'99+':count}</span>`:''}</div>
      </div>
    </div>`;
  }).join('') || '<div style="padding:24px;color:var(--muted);text-align:center">No business numbers yet.<br>Add one in the console.</div>';
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

/* How many messages the conversation opens with, and how many each "load
   earlier" fetch adds. A long conversation used to render every message on
   every event: 200 rows of innerHTML plus a JSON.stringify of each payload,
   re-run from scratch each time a single message arrived. */
const PAGE_SIZE = 40;

function showMessageSkeleton(){
  const box = $('#messages');
  if(!box) return;
  // Shown only for a cold load; an incremental update already has content on
  // screen and replacing it with placeholders would flicker.
  box.innerHTML = '<div class="msg-loading">' +
    Array.from({length:6}, (_,i)=>`<div class="skeleton skeleton-msg${i%2?' alt':''}"></div>`).join('') +
    '</div>';
}

async function loadMessages(){
  if(!state.wa){ return; }
  const sequence=++state.loadSequence;
  showMessageSkeleton();
  const d = await req('/_sandbox/messages?wa_id='+encodeURIComponent(state.wa)
    +'&phone_number_id='+encodeURIComponent(state.activePhone)
    +'&limit='+PAGE_SIZE);
  if(sequence!==state.loadSequence) return;
  const all = d.data.reverse();
  state.messages = new Map(all.map(message=>[message.id,message]));
  state.hasMore = !!d.has_more;
  state.nextBefore = d.next_before || null;
  await markRead(all);
  renderMessages({scroll:'bottom'});
}

/* Opening a chat is what marks its delivered messages read, so this stays tied
   to a load rather than to rendering, which now happens far more often. */
async function markRead(messages){
  const unread = messages.filter(m=>m.direction==='outbound' && ['accepted','sent','delivered'].includes(m.status));
  if(!unread.length || state.reading) return;
  state.reading=true;
  try{
    await req(`/_sandbox/phones/${encodeURIComponent(state.wa)}/read`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone_number_id:state.activePhone})});
    unread.forEach(m=>m.status='read');
    // Reading is what clears the badge, so refresh it here rather than
    // waiting for an observer event: the read POST does not broadcast to
    // this page's own wa_id.
    await loadUnread(); renderMobiles(); renderChatList();
  }finally{ state.reading=false; }
}

/* Fetch the page of older messages before the ones already held, preserving
   the reading position: prepending content would otherwise jump the viewport
   by exactly the height of what was added. */
async function loadEarlier(){
  if(!state.hasMore || state.loadingEarlier || !state.nextBefore) return;
  state.loadingEarlier = true;
  const button = $('#load-earlier');
  if(button){ button.disabled = true; button.textContent = 'Loading…'; }
  const sequence = state.loadSequence;
  try{
    const d = await req('/_sandbox/messages?wa_id='+encodeURIComponent(state.wa)
      +'&phone_number_id='+encodeURIComponent(state.activePhone)
      +'&limit='+PAGE_SIZE+'&before='+encodeURIComponent(state.nextBefore));
    // A chat switch during the fetch invalidates this page entirely.
    if(sequence !== state.loadSequence) return;
    const older = d.data.reverse();
    const box = $('#messages');
    const anchorHeight = box.scrollHeight, anchorTop = box.scrollTop;
    const merged = new Map();
    older.forEach(m=>merged.set(m.id,m));
    state.messages.forEach((m,id)=>merged.set(id,m));
    state.messages = merged;
    state.hasMore = !!d.has_more;
    state.nextBefore = d.next_before || null;
    renderMessages({scroll:'none'});
    box.scrollTop = anchorTop + (box.scrollHeight - anchorHeight);
  }finally{
    state.loadingEarlier = false;
    const b = $('#load-earlier');
    if(b){ b.disabled = false; b.textContent = 'Load earlier messages'; }
  }
}

/* One message arrived. Merging it beats re-fetching the page: a live event
   used to trigger a full reload of every message in the conversation. */
function appendMessage(message){
  if(!message || !message.id) return;
  if(state.messages.has(message.id)){
    state.messages.set(message.id, {...state.messages.get(message.id), ...message});
  }else{
    state.messages.set(message.id, message);
  }
  const box = $('#messages');
  // Only chase the newest message if the reader is already at the bottom;
  // yanking the viewport while they are reading history is worse than a
  // missed scroll, and matches what a real client does.
  const atBottom = !box || (box.scrollHeight - box.scrollTop - box.clientHeight) < 120;
  renderMessages({scroll: atBottom ? 'bottom' : 'none'});
}

/* A status change (sent -> delivered -> read) only alters the ticks, so it
   patches the one message rather than rebuilding the transcript. */
function applyStatus(messageId, status){
  const message = state.messages.get(messageId);
  if(!message || message.status === status) return;
  message.status = status;
  const node = document.querySelector(`.msg[data-message-id="${CSS.escape(messageId)}"] .ticks`);
  if(node) node.outerHTML = ticks(status);
  else renderMessages({scroll:'none'});
}

function renderMessages(options){
  const scroll = (options||{}).scroll || 'none';
  const box = $('#messages');
  if(!box) return;
  const all = [...state.messages.values()]
    .sort((a,b)=> new Date(a.created_at)-new Date(b.created_at) || String(a.id).localeCompare(String(b.id)));
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
  // Older pages are fetched on demand, so the transcript starts with a control
  // rather than silently pretending this is the whole conversation.
  const earlier = state.hasMore
    ? `<div class="earlier-wrap"><button type="button" id="load-earlier" class="earlier-btn">Load earlier messages</button></div>`
    : '';
  let lastDay = '';
  box.innerHTML = earlier + visible.map(m=>{
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
  if(scroll==='bottom') box.scrollTop = box.scrollHeight;
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
$('#convo-search-input').addEventListener('input',()=>renderMessages({scroll:'none'}));
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

/* ===== cross-mobile live inbox =====
   The per-customer socket at /_sandbox/clients/{wa} only carries events for the
   one customer this page is acting as, so it can never tell you that a
   *different* mobile received something. /_sandbox/observer carries every
   event tagged with its wa_id, which is what makes the panel below possible
   without polling or a manual refresh. */

async function loadUnread(){
  // Unread is derived server-side from message status, so it is already correct
  // on a cold load and cannot drift from the read receipts the sandbox sends.
  try{
    const d = await req('/_sandbox/unread');
    const totals = new Map(), perChat = new Map();
    for(const row of (d.data||[])){
      totals.set(row.wa_id, (totals.get(row.wa_id)||0) + row.unread);
      // Keyed by mobile+business so the chat list can badge and sort each
      // conversation, not just the mobile as a whole.
      perChat.set(row.wa_id+'|'+row.phone_number_id, {unread:row.unread, at:row.last_at});
    }
    state.unread = totals;
    state.chatUnread = perChat;
    const activity = new Map();
    for(const row of (d.activity||[])) activity.set(row.wa_id+'|'+row.phone_number_id, row.last_at);
    state.chatActivity = activity;
  }catch{ /* leave the previous counts rather than blanking every badge */ }
}

function unreadFor(wa){ return state.unread.get(wa) || 0; }

function renderMobiles(){
  const box = $('#mobiles');
  if(!box) return;
  if(!state.users.length){ box.innerHTML = '<div class="feed-empty">No mobiles yet.</div>'; return; }
  // Most unread first, so whatever needs attention is always at the top.
  const ordered = [...state.users].sort((a,b)=>
    unreadFor(b.wa_id)-unreadFor(a.wa_id) || String(a.display_name||'').localeCompare(String(b.display_name||'')));
  box.innerHTML = ordered.map(u=>{
    const count = unreadFor(u.wa_id);
    const mine = u.wa_id===state.wa;
    return `<div class="mobile-row ${count?'unread':''} ${mine?'current':''}" data-open-wa="${esc(u.wa_id)}"
        title="${esc(u.display_name||u.wa_id)} · +${esc(u.wa_id)}">
      <div class="mobile-av" style="background:${esc(u.color||'#6a7175')}">${esc(initials(u.display_name||u.wa_id))}</div>
      <div class="mobile-main">
        <div class="mobile-name">${esc(u.display_name||u.wa_id)}${u.auto_created?'<span class="tag-auto">auto</span>':''}</div>
        <div class="mobile-num">+${esc(u.wa_id)}${mine?' · this tab':''}</div>
      </div>
      ${count?`<span class="badge-unread">${count>99?'99+':count}</span>`:''}
    </div>`;
  }).join('');
}

function businessName(phoneId){
  const p = businessPhones().find(x=>x.id===phoneId);
  return p ? p.verified_name : (phoneId || 'Business');
}
function mobileName(wa){
  const u = state.users.find(x=>x.wa_id===wa);
  return u ? (u.display_name||wa) : wa;
}

/* One arriving message, summarised for the feed. The observer payload is the
   stored message row, so it is reduced here rather than re-fetched. */
function feedEntry(wa, message, inbound, phoneNumberId){
  const value = messageText({message_type:message.message_type||message.type,
                             payload:message.payload||message});
  const text = value.kind==='media' ? (value.label + (value.caption?': '+value.caption:''))
             : value.kind==='template' ? (value.text || value.name)
             : value.text;
  // "inbound" here is the sandbox's own sense: from the simulated customer to
  // the business. The business number is on the opposite end either way.
  // An inbound Meta payload carries no recipient at all, so the event's own
  // phone_number_id is the only source for which business it reached.
  const phoneId = phoneNumberId || message.phone_number_id
    || (inbound ? message.recipient_id : message.sender_id) || '';
  return {
    wa, phoneId, inbound: !!inbound,
    kind: value.kind==='template' ? 'template' : (value.kind==='media' ? (value.mtype||'media') : ''),
    text: text || '(no body)',
    at: message.created_at || Date.now(),
  };
}

function pushFeed(entry){
  state.feed.unshift(entry);
  if(state.feed.length > FEED_LIMIT) state.feed.length = FEED_LIMIT;
  renderFeed();
}

function renderFeed(){
  const box = $('#feed');
  if(!box) return;
  if(!state.feed.length){
    box.innerHTML = '<div class="feed-empty">Waiting for messages.<br>Anything sent to any mobile shows up here.</div>';
    return;
  }
  box.innerHTML = state.feed.map(item=>{
    // The mobile is always the subject of the row; the direction rail and the
    // preposition say which way it went, so the two names never swap places
    // and the column stays scannable.
    const via = item.inbound ? 'to' : 'from';
    return `
    <button type="button" class="feed-item ${item.inbound?'is-out':'is-in'}"
            data-open-wa="${esc(item.wa)}" data-open-business="${esc(item.phoneId)}">
      <span class="feed-rail" aria-hidden="true"></span>
      <div class="feed-main">
        <div class="feed-top">
          <span class="feed-who">${esc(mobileName(item.wa))}</span>
          <span class="feed-time">${esc(fmtTime(item.at))}</span>
        </div>
        <div class="feed-sub">${esc(via)} ${esc(businessName(item.phoneId))}${item.kind?` &middot; ${esc(item.kind)}`:''}</div>
        <div class="feed-body">${esc(item.text)}</div>
      </div>
    </button>`;
  }).join('');
}

/* Clicking any row opens that mobile in its own tab, so each mobile keeps its
   own page, its own per-customer socket and its own read state. Clicking the
   mobile this tab already is just focuses the relevant chat instead. */
function openMobile(wa, phoneId){
  if(wa === state.wa){
    if(phoneId && phoneId !== state.activePhone) openChat(phoneId);
    return;
  }
  const url = new URL('/phone', location.origin);
  url.searchParams.set('phone', wa);
  if(phoneId) url.searchParams.set('business', phoneId);
  window.open(url.toString(), 'ghost-phone-'+wa);
}

document.addEventListener('click', event=>{
  const row = event.target.closest('[data-open-wa]');
  if(!row) return;
  openMobile(row.dataset.openWa, row.dataset.openBusiness || '');
});

$('#inbox-clear').addEventListener('click', ()=>{ state.feed=[]; renderFeed(); });
$('#inbox-toggle').addEventListener('click', ()=>{
  const inbox=$('#inbox'), collapsed=inbox.classList.toggle('collapsed');
  $('#inbox-toggle').textContent = collapsed ? '›' : '‹';
  try{ localStorage.setItem('ghost.inbox.collapsed', collapsed?'1':'0'); }catch{}
});
try{
  if(localStorage.getItem('ghost.inbox.collapsed')==='1'){
    $('#inbox').classList.add('collapsed'); $('#inbox-toggle').textContent='›';
  }
}catch{ /* private mode: just start expanded */ }

function observerStatus(live){
  const dot=$('#inbox-status');
  if(!dot) return;
  dot.classList.toggle('live', !!live);
  dot.title = live ? 'Live' : 'Reconnecting';
}

function connectObserver(){
  if(state.observer){ state.observer.onclose=null; state.observer.close(); }
  if(state.observerRetry) clearTimeout(state.observerRetry);
  const scheme = location.protocol==='https:'?'wss':'ws';
  state.observer = new WebSocket(`${scheme}://${location.host}/_sandbox/observer`);
  state.observer.onopen = ()=>observerStatus(true);
  state.observer.onmessage = async event=>{
    let data; try{ data = JSON.parse(event.data); }catch{ return; }
    await handleObserverEvent(data);
  };
  state.observer.onclose = ()=>{
    observerStatus(false);
    state.observerRetry = setTimeout(connectObserver, 1500);
  };
}

async function handleObserverEvent(data){
  // A mobile appearing or changing is exactly the case that used to need a
  // manual refresh, so the roster is re-fetched rather than patched: it is one
  // small request and it cannot drift from the server.
  if(['phone_created','phone_deleted','phone_updated'].includes(data.event)){
    try{
      const users = await req('/_sandbox/phones');
      state.users = users.data;
    }catch{ return; }
    await loadUnread();
    renderMobiles();
    renderChatList();
    renderFeed();
    if(data.event==='phone_created'){
      const row = document.querySelector(`.mobile-row[data-open-wa="${CSS.escape(data.wa_id||'')}"]`);
      if(row) row.classList.add('just-added');
    }
    return;
  }
  if(data.event==='message' && data.wa_id){
    // Both directions are shown: the feed is a record of traffic, not just of
    // notifications. Only inbound-from-business counts toward unread, and that
    // distinction is carried on the entry rather than by dropping messages.
    const message = data.message || {};
    const inbound = data.direction === 'inbound'
      || (message.direction||'') === 'inbound' || !!message.from;
    pushFeed(feedEntry(data.wa_id, message, inbound, data.phone_number_id));
    await loadUnread();
    renderMobiles();
    renderChatList();
    return;
  }
  if(data.event==='status' || data.event==='read'){
    await loadUnread();
    renderMobiles();
    renderChatList();
  }
}

/* ---- live updates ---- */
function connectSocket(){
  if(state.socket){ state.socket.onclose=null; state.socket.close(); }
  if(state.reconnect) clearTimeout(state.reconnect);
  if(!state.wa) return;
  const scheme = location.protocol==='https:'?'wss':'ws';
  const wa = state.wa;
  state.socket = new WebSocket(`${scheme}://${location.host}/_sandbox/clients/${encodeURIComponent(wa)}`);
  state.socket.onmessage = event=>{
    if(state.wa!==wa) return;
    let data; try{ data = JSON.parse(event.data); }catch{ return; }
    // Reloading the whole conversation per event was the real cost: a single
    // arriving message re-fetched and re-rendered every message on screen.
    if(data.event==='message' && data.message){
      const message = data.message;
      // The per-customer socket sends the Meta wire shape for inbound, which
      // has no direction or timestamps the transcript needs, so those are
      // filled from what the event does carry.
      appendMessage(message.id ? {
        id: message.id,
        direction: data.direction || message.direction || (message.from ? 'inbound' : 'outbound'),
        message_type: message.message_type || message.type,
        payload: message.payload || message,
        status: message.status || 'delivered',
        created_at: message.created_at
          || (message.timestamp ? new Date(Number(message.timestamp)*1000).toISOString() : new Date().toISOString()),
        sender_id: message.sender_id, recipient_id: message.recipient_id,
      } : message);
      // Arriving while the chat is open means it has been seen. loadMessages()
      // used to do this as a side effect of its full reload; appending has to
      // do it explicitly, or the ticks never reach "read" and the badge sticks.
      markRead([...state.messages.values()]);
      return;
    }
    if(data.event==='status' && data.message_id){ applyStatus(data.message_id, data.status); return; }
    if(data.event==='read'){ loadMessages(); }
  };
  state.socket.onclose = ()=>{ if(state.wa===wa) state.reconnect=setTimeout(connectSocket,1500); };
}

$('#messages').addEventListener('click', event=>{
  if(event.target.closest('#load-earlier')) loadEarlier();
});
$('#search').addEventListener('input', renderChatList);

// keep business/customer names in sync with edits made in the console
async function refreshMeta(){
  try{
    const [biz,users] = await Promise.all([req('/_sandbox/businesses'), req('/_sandbox/phones')]);
    state.businesses = biz.data; state.users = users.data;
    const me = state.users.find(u=>u.wa_id===state.wa);
    applyMeAvatar(me);
    renderChatList();
    if(state.activePhone){
      const p = businessPhones().find(x=>x.id===state.activePhone);
      if(p){ $('#convo-name').textContent = p.verified_name; $('#convo-avatar').textContent = initials(p.verified_name); $('#convo-status').textContent = '+'+p.display_phone_number; }
    }
  }catch{}
}

window.addEventListener('focus', refreshMeta);
boot();
