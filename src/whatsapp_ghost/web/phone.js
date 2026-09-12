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

function renderTemplateHeaderMedia(tpl){
  const header = (tpl.components || []).find(c => (c.type||'').toLowerCase() === 'header');
  const parameter = header?.parameters?.[0] || {};
  const mediaType = ['image','video','document'].find(type => parameter.type === type && parameter[type]);
  if(!mediaType) return null;
  const media = parameter[mediaType] || {};
  const stored = media.id && state.config.access_token
    ? `/_sandbox/media/${encodeURIComponent(media.id)}?access_token=${encodeURIComponent(state.config.access_token)}` : '';
  return {mtype:mediaType, src:media.link || stored, label:{image:'Photo',video:'Video',document:'Document'}[mediaType]};
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
      if(!/^(https?:\/\/|tel:)/i.test(href))
        return `<button type="button" class="tpl-button" disabled title="Unsupported button URL">↗ ${esc(label)}</button>`;
      return `<a class="tpl-button" href="${esc(href)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">↗ ${esc(label)}</a>`;
    }
    if(type==='PHONE_NUMBER'){
      const phone=String(button.phone_number||'').replace(/[^+\d]/g,'');
      return `<a class="tpl-button" href="tel:${esc(phone)}" onclick="event.stopPropagation()">☎ ${esc(label)}</a>`;
    }
    if(type==='VOICE_CALL'){
      return `<button type="button" class="tpl-button" data-template-reply="${esc(label)}" data-template-label="${esc(label)}">☎ ${esc(label)}</button>`;
    }
    if(type==='COPY_CODE'){
      const code=String((Array.isArray(button.example)?button.example[0]:button.example) ?? parameter.coupon_code ?? '');
      return `<button type="button" class="tpl-button" data-copy-code="${esc(code)}" data-template-label="${esc(label)}" onclick="event.stopPropagation();navigator.clipboard&&navigator.clipboard.writeText('${esc(code)}')">⧉ ${esc(label)}</button>`;
    }
    if(type==='FLOW'){
      return `<button type="button" class="tpl-button" data-template-reply="${esc(button.flow_id||button.flow_name||label)}" data-template-label="${esc(label)}">☷ ${esc(button.flow_cta||label)}</button>`;
    }
    if(type==='CATALOG'||type==='MPM'){
      return `<button type="button" class="tpl-button" data-template-reply="${esc(label)}" data-template-label="${esc(label)}">▤ ${esc(label)}</button>`;
    }
    const payload=parameter.payload || parameter.text || label;
    return `<button type="button" class="tpl-button" data-template-reply="${esc(payload)}" data-template-label="${esc(label)}">${esc(label)}</button>`;
  }).filter(Boolean);
}

/* ---- clickable links in message bodies ----
   Bodies are escaped and injected as HTML, so a URL in a message arrived as
   inert grey text: not highlighted, not clickable. This escapes first and only
   then wraps the matched spans in anchors, so the text can never inject markup.
   Matches http(s):// and bare www. URLs, plus e-mail addresses. */
const LINK_PATTERN = /\b(?:https?:\/\/|www\.)[^\s<>"']+|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/gi;

/* Sentence punctuation sits against the end of a URL far more often than it is
   part of one, and a closing bracket only belongs to the URL if it opened
   inside it - "(see https://x.com/a)" must not swallow the final paren. */
function trimUrlTail(token){
  let end = token.length;
  while(end > 0){
    const char = token[end-1];
    if('.,;:!?"\''.includes(char)){ end--; continue; }
    if(char === ')' || char === ']' || char === '}'){
      const open = {')':'(', ']':'[', '}':'{'}[char];
      const body = token.slice(0, end);
      const opens = body.split(open).length - 1, closes = body.split(char).length - 1;
      if(closes > opens){ end--; continue; }
    }
    break;
  }
  return token.slice(0, end);
}

function linkify(value){
  const text = String(value ?? '');
  let html = '', cursor = 0;
  LINK_PATTERN.lastIndex = 0;
  for(let match; (match = LINK_PATTERN.exec(text)) !== null; ){
    const token = trimUrlTail(match[0]);
    if(!token){ LINK_PATTERN.lastIndex = match.index + match[0].length; continue; }
    const isEmail = token.includes('@') && !/^https?:\/\//i.test(token) && !/^www\./i.test(token);
    const href = isEmail ? 'mailto:'+token
      : (/^https?:\/\//i.test(token) ? token : 'https://'+token);
    html += esc(text.slice(cursor, match.index));
    // stopPropagation: the bubble itself toggles the raw payload on click, so
    // without it following a link also flips the JSON view open underneath.
    html += `<a class="msg-link" href="${esc(href)}" target="_blank" rel="noopener noreferrer"`
      + ` onclick="event.stopPropagation()">${esc(token)}</a>`;
    cursor = match.index + token.length;
    LINK_PATTERN.lastIndex = cursor;
  }
  return html + esc(text.slice(cursor));
}

/* A location bubble: map plate, then name over address, exactly the stack
   WhatsApp uses. The plate is drawn inline rather than fetched: map tiles are a
   network dependency, and a sandbox whose whole point is running offline must
   not render a broken image when there is no route to a tile server. The pin
   sits at the real fractional position of the coordinate within its degree
   square, so two nearby places do not draw an identical picture.
   Tapping opens OpenStreetMap, which needs no key and no account. */
function locationPlate(lat, lng){
  const x = 8 + ((lng + 180) % 1) * 84, y = 8 + ((lat + 90) % 1) * 60;
  return `<svg class="loc-plate" viewBox="0 0 100 76" aria-hidden="true">`
    + `<rect width="100" height="76" fill="#e8ece9"/>`
    + `<path d="M-10 58 L40 26 L72 42 L110 18" fill="none" stroke="#cfd8d3" stroke-width="9"/>`
    + `<path d="M-10 20 L28 34 L52 22 L110 52" fill="none" stroke="#dde4e0" stroke-width="6"/>`
    + `<rect x="-10" y="60" width="120" height="26" fill="#cfe3f0"/>`
    + `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="13" fill="#25d366" opacity=".22"/>`
    + `<path d="M${x.toFixed(1)} ${(y+7).toFixed(1)}c0 0-5-5.2-5-8a5 5 0 0 1 10 0c0 2.8-5 8-5 8z" fill="#d3312a"/>`
    + `</svg>`;
}

function locationHtml(val){
  const href = `https://www.openstreetmap.org/?mlat=${val.lat}&mlon=${val.lng}#map=17/${val.lat}/${val.lng}`;
  const coords = `${val.lat.toFixed(5)}, ${val.lng.toFixed(5)}`;
  // stopPropagation: the bubble toggles the raw payload on click, so without it
  // opening the map also flips the JSON view open underneath.
  return `<a class="loc-card" href="${esc(href)}" target="_blank" rel="noopener noreferrer"`
    + ` onclick="event.stopPropagation()" title="Open in OpenStreetMap">`
    + locationPlate(val.lat, val.lng)
    + `<span class="loc-meta">`
    + (val.name ? `<strong>${esc(val.name)}</strong>` : '')
    + (val.address ? `<span class="loc-address">${esc(val.address)}</span>` : '')
    + `<span class="loc-coords">${esc(coords)}</span>`
    + `</span></a>`;
}

/* Interactive list / reply-button messages. An inbound *_reply is the
   customer's answer and renders as plain text; an outbound one carries the
   choices, which render as tappable rows that post the reply back. */
function interactiveMessage(payload){
  const interactive = payload.interactive || {};
  const reply = interactive.list_reply || interactive.button_reply;
  if(reply){
    return {kind:'text', text:reply.title || reply.id || 'Interactive reply'};
  }
  const action = interactive.action || {};
  const options = [];
  for(const section of (action.sections || [])){
    for(const row of (section.rows || [])){
      if(row.id && row.title){
        options.push({type:'list_reply', id:String(row.id), title:String(row.title), description:String(row.description || '')});
      }
    }
  }
  for(const button of (action.buttons || [])){
    const value = button.reply || {};
    if(value.id && value.title){
      options.push({type:'button_reply', id:String(value.id), title:String(value.title), description:''});
    }
  }
  const header = interactive.header?.text || '';
  const body = interactive.body?.text || '';
  const footer = interactive.footer?.text || '';
  return {kind:'interactive', text:[header,body,footer].filter(Boolean).join('\n'), options};
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
    return {kind:'template', name, text: renderTemplateBody(name, tpl), buttons:renderTemplateButtons(name,tpl), headerMedia:renderTemplateHeaderMedia(tpl)};
  }
  if(t === 'location'){
    const loc = p.location || {};
    const lat = Number(loc.latitude), lng = Number(loc.longitude);
    if(!Number.isFinite(lat) || !Number.isFinite(lng)) return {kind:'text', text:'[location]'};
    // The searchable/preview text is the place name when there is one, because
    // that is what WhatsApp shows in the chat list - a raw coordinate pair is
    // unrecognisable at a glance and unsearchable by the name you know it by.
    const label = loc.name || `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    return {kind:'location', lat, lng, name:loc.name||'', address:loc.address||'', text:label};
  }
  if(t === 'interactive') return interactiveMessage(p);
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

/* Ticks describe the simulated customer's device, NOT your webhook endpoint.
   A message can be double-ticked and still have reached no integration at all,
   because a webhook with no subscriber is stored "unrouted" rather than sent.
   The titles say so, and the hover diagnostics separate the two explicitly. */
function ticks(status){
  if(status==='read')      return '<span class="ticks read" title="Read by the simulated phone. Webhook delivery to your server is shown separately - hover this message.">&#10003;&#10003;</span>';
  if(status==='delivered') return '<span class="ticks" title="Delivered to the simulated phone. Webhook delivery to your server is shown separately - hover this message.">&#10003;&#10003;</span>';
  if(status==='sent')      return '<span class="ticks" title="Accepted by the sandbox, not yet delivered to the simulated phone.">&#10003;</span>';
  if(status==='failed')    return '<span class="ticks" style="color:#e5484d" title="The sandbox could not deliver this message.">!</span>';
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

function isUnknownTemplate(message){
  if(message.message_type !== 'template') return false;
  const template=(message.payload || {}).template || {};
  const name=template.name;
  const language=template.language?.code || '';
  return !!name && !state.templates[name+'|'+language] && !state.templates[name];
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
  // Which mobile this tab is acting as. With several simulator tabs open it was
  // otherwise only inferable from the avatar's two letters.
  const name = $('#me-name'), number = $('#me-num');
  if(name) name.textContent = me?.display_name || state.wa || 'Test customer';
  if(number) number.textContent = state.wa ? '+'+state.wa : '';
}

/* A confirmation that does not steal focus the way alert() does. */
function toast(message, bad=false){
  let box = $('#p-toast');
  if(!box){
    box = document.createElement('div');
    box.id = 'p-toast'; box.className = 'p-toast';
    document.body.appendChild(box);
  }
  box.textContent = message;
  box.classList.toggle('bad', bad);
  box.classList.add('show');
  clearTimeout(box._timer);
  box._timer = setTimeout(()=>box.classList.remove('show'), bad ? 6000 : 2600);
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
  document.querySelectorAll('.chat-row').forEach(r=>r.classList.remove('active'));
  document.querySelectorAll('.chat-row').forEach(r=>{ if(r.getAttribute('onclick')?.includes(phoneId)) r.classList.add('active'); });
  const url = new URL(location); url.searchParams.set('business', phoneId); url.searchParams.set('phone', state.wa); history.replaceState(null,'',url);
  // The header buttons, the size warning and any open analytics all describe
  // the chat that was open, so they follow the switch rather than lag it.
  syncConvoActions();
  renderBloatWarning();
  closeStats();
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
  // A template can be seeded while the simulator is already open. Refresh its
  // definitions before rendering instead of showing the bare template name
  // until the user manually reloads the entire page.
  if(all.some(isUnknownTemplate)) await loadTemplateDefinitions();
  await markRead(all);
  renderMessages({scroll:'bottom'});
}

/* Opening a chat is what marks its delivered messages read, so this stays tied
   to a load rather than to rendering, which now happens far more often. */
async function markRead(messages){
  const unread = messages.filter(m=>m.direction==='outbound' && ['accepted','sent','delivered'].includes(m.status));
  if(!unread.length) return;
  // A read already in flight would otherwise swallow this one: the guard used
  // to drop the call entirely, so a message arriving mid-read stayed unread
  // until the next full load. Wait for the current one, then re-check.
  if(state.reading){
    await state.reading;
    const still = [...state.messages.values()].filter(
      m=>m.direction==='outbound' && ['accepted','sent','delivered'].includes(m.status));
    if(!still.length) return;
  }
  let settle;
  state.reading = new Promise(resolve=>{ settle = resolve; });
  try{
    await req(`/_sandbox/phones/${encodeURIComponent(state.wa)}/read`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone_number_id:state.activePhone})});
    unread.forEach(m=>m.status='read');
    // Reading is what clears the badge, so refresh it here rather than
    // waiting for an observer event: the read POST does not broadcast to
    // this page's own wa_id.
    await loadUnread(); renderMobiles(); renderChatList();
  }finally{ state.reading=null; settle(); }
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
  // The diagnostics for this message just changed - it gained a status hop and
  // probably a webhook - so a cached copy would show the state before the hop.
  invalidateDiagnostics(messageId);
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
      const header=val.headerMedia?.mtype==='image' && val.headerMedia.src
        ? `<img class="media-thumb tpl-header-media" src="${esc(val.headerMedia.src)}" alt="Approved arrival selfie">`
        : val.headerMedia ? `<span class="tpl-tag">${esc(val.headerMedia.label).toUpperCase()}</span>` : '';
      bodyHtml = `${header}<span class="tpl-tag">TEMPLATE</span><span class="body">${linkify(val.text || val.name)}</span>${buttons}`;
    } else if(val.kind==='location'){
      bodyHtml += locationHtml(val);
    } else if(val.kind==='interactive'){
      const options=(val.options||[]).map(option=>
        `<button type="button" class="interactive-option" data-interactive-reply="${esc(option.id)}" data-interactive-title="${esc(option.title)}" data-interactive-description="${esc(option.description)}" data-interactive-type="${esc(option.type)}"><strong>${esc(option.title)}</strong>${option.description?`<span>${esc(option.description)}</span>`:''}</button>`
      ).join('');
      bodyHtml += `<span class="body">${linkify(val.text || 'Choose an option')}</span>${options?`<div class="interactive-options">${options}</div>`:''}`;
    } else if(val.kind==='media'){
      if(val.mtype==='image' && val.src) bodyHtml += `<img class="media-thumb" src="${esc(val.src)}" alt="">`;
      else bodyHtml += `<span class="tpl-tag">${esc(val.label).toUpperCase()}</span>`;
      if(val.caption) bodyHtml += `<span class="body">${linkify(val.caption)}</span>`;
    } else {
      bodyHtml += `<span class="body">${linkify(val.text)}</span>`;
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
  const option=event.target.closest('[data-interactive-reply]');
  if(!option) return;
  event.stopPropagation();
  option.disabled=true;
  const reply={id:option.dataset.interactiveReply,title:option.dataset.interactiveTitle};
  if(option.dataset.interactiveDescription) reply.description=option.dataset.interactiveDescription;
  const replyType=option.dataset.interactiveType==='button_reply'?'button_reply':'list_reply';
  try{
    await sendInbound({type:'interactive',interactive:{type:replyType,[replyType]:reply}});
  }catch(error){
    alert(error.message);
    option.disabled=false;
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
/* ---- new chat / pane menu ----
   Both buttons used to just focus the search box, which is indistinguishable
   from them being broken. The chat button now picks a business number to talk
   to, the way the real client picks a contact. */
function closePanePopovers(){
  $('#new-chat-menu').classList.add('hidden');
  $('#pane-menu').classList.add('hidden');
}

function renderNewChatMenu(){
  const list = $('#new-chat-list');
  const phones = businessPhones();
  if(!phones.length){
    list.innerHTML = '<div class="pp-empty">No business numbers registered yet. Add a sender in the console.</div>';
    return;
  }
  list.innerHTML = phones.map(p=>`
    <button type="button" data-start-chat="${esc(p.id)}">
      <span class="pp-avatar">${esc(initials(p.verified_name))}</span>
      <span class="pp-main">
        <span class="pp-name">${esc(p.verified_name)}</span>
        <span class="pp-sub">+${esc(p.display_phone_number)}</span>
      </span>
    </button>`).join('');
}

$('#new-chat-btn').addEventListener('click', event=>{
  event.stopPropagation();
  const menu = $('#new-chat-menu'), opening = menu.classList.contains('hidden');
  closePanePopovers();
  if(opening){ renderNewChatMenu(); menu.classList.remove('hidden'); }
});
$('#new-chat-list').addEventListener('click', event=>{
  const button = event.target.closest('[data-start-chat]');
  if(!button) return;
  closePanePopovers();
  openChat(button.dataset.startChat);
});
$('#pane-menu-btn').addEventListener('click', event=>{
  event.stopPropagation();
  const menu = $('#pane-menu'), opening = menu.classList.contains('hidden');
  closePanePopovers();
  if(opening) menu.classList.remove('hidden');
});
$('#menu-add-number').addEventListener('click', ()=>{ closePanePopovers(); openMobileSheet(null); });
$('#menu-switch-number').addEventListener('click', ()=>{
  closePanePopovers();
  $('#mobile-search').focus();
  $('#inbox').classList.remove('collapsed');
  $('#inbox-toggle').textContent = '‹';
});
$('#menu-clear-every-chat').addEventListener('click', ()=>{ closePanePopovers(); clearChat(null); });
document.addEventListener('click', event=>{
  if(!event.target.closest('.pane-popover') && !event.target.closest('#new-chat-btn')
     && !event.target.closest('#pane-menu-btn')) closePanePopovers();
});
$('#convo-search-btn').addEventListener('click',()=>{$('#convo-search').classList.toggle('hidden');$('#convo-search-input').focus();});
$('#close-convo-search').addEventListener('click',()=>{$('#convo-search-input').value='';$('#convo-search').classList.add('hidden');loadMessages();});
$('#convo-search-input').addEventListener('input',()=>renderMessages({scroll:'none'}));
$('#convo-menu-btn').addEventListener('click',event=>{event.stopPropagation();$('#convo-menu').classList.toggle('hidden');});
$('#menu-pin-chat').addEventListener('click',async ()=>{$('#convo-menu').classList.add('hidden');await togglePin(state.activePhone);renderChatList();});
$('#menu-contact-info').addEventListener('click',()=>{const phone=businessPhones().find(item=>item.id===state.activePhone);alert(phone?`${phone.verified_name}\n+${phone.display_phone_number}\n${phone.business_name}`:'Contact unavailable');});

/* Erase a transcript. phoneId null clears every chat this mobile has.
   The conversation itself survives, so the 24-hour window and the pin do too:
   clearing a chat removes the messages, not the contact. */
async function clearChat(phoneId){
  if(!state.wa){ toast('No test number selected', true); return; }
  const label = phoneId ? `the chat with ${businessName(phoneId)}` : 'every chat on this number';
  if(!confirm(`Clear ${label}?\nThe messages are deleted for good. The chat itself stays.`)) return;
  const query = phoneId ? '?phone_number_id='+encodeURIComponent(phoneId) : '';
  try{
    const d = await req('/_sandbox/phones/'+encodeURIComponent(state.wa)+'/messages'+query, {method:'DELETE'});
    toast(d.deleted ? `Cleared ${d.deleted} message${d.deleted===1?'':'s'}` : 'Nothing to clear');
    await applyChatCleared(phoneId);
  }catch(e){ toast(e.message || 'Could not clear this chat', true); }
}

/* Bring this tab in line with a clear, whether it did it or another tab did. */
async function applyChatCleared(phoneId){
  if(!phoneId || phoneId === state.activePhone){
    state.messages.clear();
    state.hasMore = false; state.nextBefore = null;
    renderMessages({scroll:'bottom'});
  }
  // Previews and badges are derived from the messages that just went away.
  state.feed = state.feed.filter(item => item.wa !== state.wa || (phoneId && item.phoneId !== phoneId));
  renderFeed();
  await loadUnread();
  renderMobiles();
  renderChatList();
}

$('#menu-clear-chat').addEventListener('click', ()=>{
  $('#convo-menu').classList.add('hidden');
  clearChat(state.activePhone);
});
$('#convo-clear-btn').addEventListener('click', ()=>clearChat(state.activePhone));
$('#convo-pin-btn').addEventListener('click', async ()=>{
  await togglePin(state.activePhone);
  syncConvoActions();
});

/* Keep the header buttons showing the chat's actual state. */
function syncConvoActions(){
  const pinned = state.pinned.has(state.activePhone);
  const button = $('#convo-pin-btn');
  if(button){
    button.classList.toggle('on', pinned);
    button.setAttribute('aria-pressed', String(pinned));
    button.title = pinned ? 'Unpin chat' : 'Pin chat';
  }
  const entry = $('#menu-pin-chat');
  if(entry) entry.textContent = pinned ? 'Unpin chat' : 'Pin chat';
}

/* ---- per-chat analytics ----
   "When did this conversation actually happen" is not answerable by scrolling a
   thousand bubbles. The server aggregates, so this stays instant at any size. */
const DAY_START = 6, DAY_END = 18;   // local hours counted as daylight

function hourLabel(hour){
  const suffix = hour < 12 ? 'am' : 'pm';
  const value = hour % 12 === 0 ? 12 : hour % 12;
  return value + suffix;
}

function statBar(count, peak){
  // A non-zero bucket always gets a visible sliver, or a quiet hour beside a
  // busy one reads as no data rather than as little data.
  return count ? Math.max(4, Math.round((count / peak) * 100)) : 0;
}

function renderHourHistogram(byHour){
  const peak = Math.max(...byHour, 1);
  const bars = byHour.map((count, hour)=>{
    const night = hour < DAY_START || hour >= DAY_END;
    return `<div class="hb ${night?'night':'day'}" style="--h:${statBar(count,peak)}%"
      title="${hourLabel(hour)} · ${count} message${count===1?'':'s'}"><span></span></div>`;
  }).join('');
  return `
    <div class="stats-card">
      <div class="stats-card-head"><b>By hour of day</b>
        <span class="stats-legend"><i class="sw day"></i>day<i class="sw night"></i>night</span></div>
      <div class="hbars">${bars}</div>
      <div class="hbars-axis"><span>12am</span><span>6am</span><span>12pm</span><span>6pm</span><span>11pm</span></div>
    </div>`;
}

function renderDayTimeline(byDay){
  if(!byDay.length) return '';
  const peak = Math.max(...byDay.map(d=>d.total), 1);
  // Newest last so the row reads left to right as time passing, and capped so
  // a year-long chat does not render 365 unreadable slivers.
  const shown = byDay.slice(-60);
  const bars = shown.map(day=>{
    const label = new Date(day.day + 'T00:00:00').toLocaleDateString([], {month:'short', day:'numeric'});
    return `<div class="tl-col" title="${esc(label)} · ${day.total} message${day.total===1?'':'s'} (${day.inbound} sent, ${day.outbound} received)">
      <div class="tl-stack" style="--h:${statBar(day.total,peak)}%">
        <span class="tl-in" style="flex:${day.inbound||0}"></span>
        <span class="tl-out" style="flex:${day.outbound||0}"></span>
      </div></div>`;
  }).join('');
  const first = new Date(shown[0].day + 'T00:00:00').toLocaleDateString([], {month:'short', day:'numeric'});
  const last = new Date(shown[shown.length-1].day + 'T00:00:00').toLocaleDateString([], {month:'short', day:'numeric'});
  return `
    <div class="stats-card">
      <div class="stats-card-head"><b>Messages per day</b>
        <span class="stats-legend"><i class="sw in"></i>you<i class="sw out"></i>business</span></div>
      <div class="timeline">${bars}</div>
      <div class="hbars-axis"><span>${esc(first)}</span><span>${esc(last)}</span></div>
      ${byDay.length > shown.length ? `<div class="stats-note">Showing the most recent ${shown.length} of ${byDay.length} days.</div>` : ''}
    </div>`;
}

function renderTypeBreakdown(byType, total){
  const entries = Object.entries(byType||{}).sort((a,b)=>b[1]-a[1]);
  if(!entries.length) return '';
  return `
    <div class="stats-card">
      <div class="stats-card-head"><b>By message type</b></div>
      <div class="stats-rows">${entries.map(([type,count])=>`
        <div class="stats-row">
          <span class="stats-row-label">${esc(type)}</span>
          <span class="stats-meter"><i style="width:${total?Math.round(count/total*100):0}%"></i></span>
          <span class="stats-row-value">${count}</span>
        </div>`).join('')}</div>
    </div>`;
}

function renderStats(data){
  const body = $('#stats-body');
  if(!data.total){
    body.innerHTML = '<div class="stats-empty">No messages in this chat yet.</div>';
    $('#stats-range').textContent = '';
    return;
  }
  const first = new Date(data.first_at), last = new Date(data.last_at);
  const peakHour = data.by_hour.indexOf(Math.max(...data.by_hour));
  const nightTotal = data.by_hour.reduce((sum,count,hour)=>
    sum + ((hour < DAY_START || hour >= DAY_END) ? count : 0), 0);
  const days = Math.max(1, data.by_day.length);
  const fmtDate = d => d.toLocaleDateString([], {month:'short', day:'numeric'}) + ', ' + fmtTime(d);
  $('#stats-range').textContent = fmtDate(first) + ' – ' + fmtDate(last);
  body.innerHTML = `
    <div class="stats-tiles">
      <div class="stats-tile"><small>Messages</small><strong>${data.total}</strong></div>
      <div class="stats-tile"><small>You sent</small><strong>${data.inbound}</strong></div>
      <div class="stats-tile"><small>Received</small><strong>${data.outbound}</strong></div>
      <div class="stats-tile"><small>Busiest hour</small><strong>${esc(hourLabel(peakHour))}</strong></div>
      <div class="stats-tile"><small>Per active day</small><strong>${(data.total/days).toFixed(1)}</strong></div>
      <div class="stats-tile"><small>Overnight</small><strong>${Math.round(nightTotal/data.total*100)}%</strong></div>
    </div>
    ${renderHourHistogram(data.by_hour)}
    ${renderDayTimeline(data.by_day)}
    ${renderTypeBreakdown(data.by_type, data.total)}`;
}

async function openStats(){
  if(!state.wa || !state.activePhone) return;
  const panel = $('#convo-stats');
  panel.classList.remove('hidden');
  $('#stats-body').innerHTML = '<div class="stats-empty"><span class="spinner"></span> Crunching this chat…</div>';
  try{
    // getTimezoneOffset() counts minutes behind UTC, so it is negated to become
    // "minutes to add to UTC", which is what the endpoint buckets with.
    const offset = -new Date().getTimezoneOffset();
    const data = await req('/_sandbox/phones/'+encodeURIComponent(state.wa)+'/analytics'
      + '?phone_number_id=' + encodeURIComponent(state.activePhone)
      + '&tz_offset=' + offset);
    renderStats(data);
  }catch(e){
    $('#stats-body').innerHTML = `<div class="stats-empty">${esc(e.message||'Could not load analytics')}</div>`;
  }
}

function closeStats(){ $('#convo-stats').classList.add('hidden'); }

$('#convo-stats-btn').addEventListener('click', ()=>{
  const panel = $('#convo-stats');
  if(panel.classList.contains('hidden')) openStats(); else closeStats();
});
$('#stats-close').addEventListener('click', closeStats);

/* ---- hover diagnostics ----
   "It says sent, so why did my server never get it?" used to mean opening the
   webhook page and searching for the id by hand. Hovering a bubble now answers
   it in place: the status hops with their delays, and every webhook the message
   produced with its HTTP result. */
const diagnosticsCache = new Map();
const diagnosticsInflight = new Map();
let diagnosticsTimer = null, diagnosticsFor = null;

/* One request per message in flight at a time. Without this a hover that lands
   while an earlier fetch is still running stacks a second identical request. */
function fetchDiagnostics(id){
  if(diagnosticsInflight.has(id)) return diagnosticsInflight.get(id);
  const pending = req('/_sandbox/messages/'+encodeURIComponent(id)+'/diagnostics')
    .catch(error=>({error:error.message||'Could not load diagnostics'}))
    .then(data=>{ diagnosticsCache.set(id, data); diagnosticsInflight.delete(id); return data; });
  diagnosticsInflight.set(id, pending);
  return pending;
}

/* Drop a cached diagnostic and repaint if the popover is currently showing it. */
function invalidateDiagnostics(messageId){
  diagnosticsCache.delete(messageId);
  if(diagnosticsFor !== messageId) return;
  const bubble = document.querySelector(`.msg[data-message-id="${CSS.escape(messageId)}"]`);
  if(bubble) showDiagnostics(bubble);
}

function relTime(ms){
  if(ms === null || ms === undefined) return '—';
  if(ms < 1000) return Math.round(ms)+'ms';
  if(ms < 60000) return (ms/1000).toFixed(ms < 10000 ? 2 : 1)+'s';
  return Math.round(ms/60000)+'m';
}

const VERDICTS = {
  delivered:   {tone:'ok',   text:'Webhooks delivered'},
  pending:     {tone:'warn', text:'Webhook still pending'},
  unrouted:    {tone:'warn', text:'No subscriber — event stored, never sent'},
  webhook_failed:{tone:'bad', text:'Webhook delivery failed'},
  no_webhook:  {tone:'warn', text:'No webhook produced for this message'},
};

function diagnosticsHtml(data){
  const verdict = VERDICTS[data.verdict] || {tone:'warn', text:data.verdict};
  // The question this answers: "it shows two ticks, so why did my server never
  // see it?" Ticks are the simulated phone; webhooks are your integration. They
  // are independent, and only the mismatch is worth calling out.
  const ticked = ['delivered','read'].includes(data.status);
  const note = (ticked && data.verdict !== 'delivered')
    ? `<div class="dbg-note">The ticks mean the simulated phone received this.
       They say nothing about your integration: this message's webhook was
       <b>${esc(data.verdict === 'no_webhook' ? 'never queued' : data.verdict)}</b>,
       so your server was not told.</div>`
    : '';
  const hops = data.events.length
    ? data.events.map(event=>`<div class="dbg-hop">
        <span class="dbg-dot ${esc(event.status)}"></span>
        <span class="dbg-hop-name">${esc(event.status)}</span>
        <span class="dbg-hop-delay">+${esc(relTime(event.delay_ms))}</span>
      </div>`).join('')
    : '<div class="dbg-none">No status transitions recorded.</div>';
  const hooks = data.deliveries.length
    ? data.deliveries.map(hook=>{
        // A failure with no status code never reached the server at all - a
        // timeout or a refused connection - which reads very differently from a
        // server that answered and rejected the event. Saying "—" hid that.
        const code = hook.last_status_code ? 'HTTP '+hook.last_status_code
          : hook.status==='unrouted' ? 'not sent'
          : hook.status==='failed' ? 'no response'
          : hook.status==='pending' ? 'in flight' : '—';
        return `<div class="dbg-hook">
          <div class="dbg-hook-top">
            <span class="dbg-pill ${esc(hook.status)}">${esc(hook.status)}</span>
            <span class="dbg-hook-event">${esc(hook.event_type||'event')}</span>
            <span class="dbg-hook-code">${esc(code)}</span>
          </div>
          <div class="dbg-hook-meta">
            queued +${esc(relTime(hook.queued_delay_ms))}
            ${hook.delivered_at ? ' · delivered +'+esc(relTime(hook.delivered_delay_ms)) : ''}
            ${hook.attempt_count > 1 ? ' · '+hook.attempt_count+' attempts' : ''}
          </div>
          ${hook.destination_url ? `<div class="dbg-hook-url">${esc(hook.destination_url)}</div>` : ''}
          ${hook.last_error ? `<div class="dbg-error">${esc(hook.last_error)}</div>` : ''}
          ${hook.last_response_body && !hook.last_error ? `<div class="dbg-response">${esc(String(hook.last_response_body).slice(0,300))}</div>` : ''}
        </div>`;
      }).join('')
    : '<div class="dbg-none">Nothing was queued for delivery.</div>';
  return `
    <div class="dbg-head">
      <span class="dbg-verdict ${esc(verdict.tone)}">${esc(verdict.text)}</span>
      ${note}
    </div>
    <div class="dbg-grid">
      <span>Status</span><b>${esc(data.status)}${data.failure_code?` (code ${esc(data.failure_code)})`:''}</b>
      <span>Type</span><b>${esc(data.message_type)}</b>
      <span>Direction</span><b>${data.direction==='inbound'?'you → business':'business → you'}</b>
      <span>Created</span><b>${esc(new Date(data.created_at).toLocaleTimeString())}</b>
    </div>
    <div class="dbg-section">Status timeline</div>
    <div class="dbg-hops">${hops}</div>
    <div class="dbg-section">Webhooks</div>
    <div class="dbg-hooks">${hooks}</div>
    <div class="dbg-foot">${esc(data.id)}</div>`;
}

function diagnosticsBox(){
  let box = $('#msg-debug');
  if(!box){
    box = document.createElement('div');
    box.id = 'msg-debug'; box.className = 'msg-debug';
    // Hovering onto the popover itself must not dismiss it, or a scrollable
    // failure message would be unreadable.
    box.addEventListener('mouseenter', ()=>clearTimeout(box._hide));
    box.addEventListener('mouseleave', hideDiagnostics);
    document.body.appendChild(box);
  }
  return box;
}

function placeDiagnostics(box, bubble){
  const rect = bubble.getBoundingClientRect();
  const width = box.offsetWidth, height = box.offsetHeight;
  // Prefer above the bubble; flip below when there is no room, and keep the
  // whole card inside the viewport either way.
  let top = rect.top - height - 8;
  if(top < 8) top = Math.min(rect.bottom + 8, window.innerHeight - height - 8);
  let left = rect.left + rect.width/2 - width/2;
  left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
  box.style.top = top+'px';
  box.style.left = left+'px';
}

async function showDiagnostics(bubble){
  const id = bubble.dataset.messageId;
  if(!id) return;
  diagnosticsFor = id;
  const box = diagnosticsBox();
  clearTimeout(box._hide);
  const paint = data => {
    if(diagnosticsFor !== id) return;   // pointer moved on while we fetched
    box.innerHTML = data.error
      ? `<div class="dbg-loading">${esc(data.error)}</div>`
      : diagnosticsHtml(data);
    box.classList.add('show');
    placeDiagnostics(box, bubble);
  };
  // A webhook outcome lands long after the message itself stops changing: a
  // retry, a timeout or a late 2xx produces no status hop, so nothing marks the
  // cache stale and a cached copy keeps describing a delivery that has since
  // failed or succeeded. Every hover therefore refetches. The cached copy is
  // painted first so the popover still opens instantly instead of flashing a
  // spinner over information we already have.
  if(diagnosticsCache.has(id)) paint(diagnosticsCache.get(id));
  else{
    box.innerHTML = '<div class="dbg-loading"><span class="spinner"></span> Loading diagnostics…</div>';
    box.classList.add('show');
    placeDiagnostics(box, bubble);
  }
  paint(await fetchDiagnostics(id));
}

function hideDiagnostics(){
  const box = $('#msg-debug');
  if(!box) return;
  diagnosticsFor = null;
  box._hide = setTimeout(()=>box.classList.remove('show'), 120);
}

$('#messages').addEventListener('mouseover', event=>{
  const bubble = event.target.closest('.msg[data-message-id]');
  if(!bubble) return;
  if(bubble.dataset.messageId === diagnosticsFor) return;
  clearTimeout(diagnosticsTimer);
  // A short delay so sweeping the pointer across a transcript does not fire a
  // request per bubble it crosses.
  diagnosticsTimer = setTimeout(()=>showDiagnostics(bubble), 260);
});
$('#messages').addEventListener('mouseout', event=>{
  const bubble = event.target.closest('.msg[data-message-id]');
  if(!bubble) return;
  if(event.relatedTarget && bubble.contains(event.relatedTarget)) return;
  clearTimeout(diagnosticsTimer);
  hideDiagnostics();
});
// A status change invalidates what the popover last showed for that message.
$('#messages').addEventListener('scroll', ()=>{ clearTimeout(diagnosticsTimer); hideDiagnostics(); });
document.addEventListener('click',event=>{if(!event.target.closest('#convo-menu')&&!event.target.closest('#convo-menu-btn'))$('#convo-menu').classList.add('hidden');});
document.addEventListener('click',event=>{if(!event.target.closest('.msg-action-menu')&&!event.target.closest('.msg-action-toggle'))document.querySelectorAll('.msg.actions-open').forEach(item=>item.classList.remove('actions-open'));});

/* ---- share a location, as the customer ---- */
// Real coordinates for the presets: a sandbox that ships 0,0 teaches nobody
// what a plausible payload looks like, and Null Island renders identically for
// every one of them.
const LOCATION_PRESETS = [
  {label:'Gateway of India', latitude:18.9220, longitude:72.8347, name:'Gateway of India', address:'Apollo Bandar, Colaba, Mumbai 400001'},
  {label:'Bengaluru airport', latitude:13.1986, longitude:77.7066, name:'Kempegowda International Airport', address:'KIAL Rd, Devanahalli, Bengaluru 560300'},
  {label:'Coordinates only', latitude:28.6129, longitude:77.2295, name:'', address:''},
];

const locSheet = $('#loc-sheet');

function closeLocationSheet(){ locSheet.classList.add('hidden'); }

function openLocationSheet(){
  $('#emoji-picker').classList.add('hidden');
  locSheet.classList.remove('hidden');
  $('#loc-lat').focus();
}

$('#loc-presets').innerHTML = LOCATION_PRESETS.map((preset, index)=>
  `<button type="button" data-loc-preset="${index}">${esc(preset.label)}</button>`).join('');

$('#loc-presets').addEventListener('click', event=>{
  const button = event.target.closest('[data-loc-preset]');
  if(!button) return;
  const preset = LOCATION_PRESETS[Number(button.dataset.locPreset)];
  $('#loc-lat').value = preset.latitude;
  $('#loc-lng').value = preset.longitude;
  $('#loc-name').value = preset.name;
  $('#loc-address').value = preset.address;
});

$('#location-btn').addEventListener('click', ()=>{
  if(locSheet.classList.contains('hidden')) openLocationSheet(); else closeLocationSheet();
});
$('#loc-cancel').addEventListener('click', closeLocationSheet);

locSheet.addEventListener('submit', async event=>{
  event.preventDefault();
  const latitude = Number($('#loc-lat').value), longitude = Number($('#loc-lng').value);
  if(!Number.isFinite(latitude) || !Number.isFinite(longitude)){
    alert('Latitude and longitude must both be numbers.');
    return;
  }
  // Meta omits name and address rather than sending empty strings, and the
  // sandbox's whole contract is that its payloads match the wire exactly.
  const location = {latitude, longitude};
  const name = $('#loc-name').value.trim(), address = $('#loc-address').value.trim();
  if(name) location.name = name;
  if(address) location.address = address;
  const button = $('#loc-send');
  button.disabled = true;
  try{
    await sendInbound({type:'location', location});
    closeLocationSheet();
    locSheet.reset();
  }catch(error){
    alert(error.message);
  }finally{
    button.disabled = false;
  }
});

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
    const activity = new Map(), perChatTotals = new Map();
    for(const row of (d.activity||[])){
      activity.set(row.wa_id+'|'+row.phone_number_id, row.last_at);
      // The endpoint already counts every message per chat for its ordering,
      // so the size warning below is free rather than another query.
      perChatTotals.set(row.wa_id+'|'+row.phone_number_id, row.total||0);
    }
    state.chatActivity = activity;
    state.chatTotals = perChatTotals;
    renderBloatWarning();
  }catch{ /* leave the previous counts rather than blanking every badge */ }
}

function unreadFor(wa){ return state.unread.get(wa) || 0; }

/* ---- oversized chat warning ----
   Past this many messages the transcript is slow to page through and the
   histogram is the only readable view of it, so clearing is offered in place
   rather than left to be discovered in a menu. */
const BLOAT_WARN_AT = 400;
const BLOAT_SEVERE_AT = 2000;
const bloatDismissed = new Set();

function chatTotal(phoneId){ return (state.chatTotals && state.chatTotals.get(chatKey(phoneId))) || 0; }

function renderBloatWarning(){
  const banner = $('#chat-bloat');
  if(!banner) return;
  const phoneId = state.activePhone;
  const total = phoneId ? chatTotal(phoneId) : 0;
  if(!phoneId || total < BLOAT_WARN_AT || bloatDismissed.has(chatKey(phoneId))){
    banner.classList.add('hidden');
    return;
  }
  const severe = total >= BLOAT_SEVERE_AT;
  banner.classList.toggle('severe', severe);
  $('#bloat-title').textContent = `${total.toLocaleString()} messages in this chat`;
  $('#bloat-sub').textContent = severe
    ? 'Paging through this is slow. Clearing it keeps the number and its 24-hour window.'
    : 'Getting long. Clearing keeps the number and its 24-hour window.';
  banner.classList.remove('hidden');
}

$('#bloat-clear').addEventListener('click', ()=>clearChat(state.activePhone));
$('#bloat-dismiss').addEventListener('click', ()=>{
  // Per chat, for this tab only: a reload is a fair place to be reminded again.
  if(state.activePhone) bloatDismissed.add(chatKey(state.activePhone));
  $('#chat-bloat').classList.add('hidden');
});

function mobileMatches(user, query){
  if(!query) return true;
  return (String(user.display_name||'') + ' ' + user.wa_id).toLowerCase().includes(query);
}

function renderMobiles(){
  const box = $('#mobiles');
  if(!box) return;
  if(!state.users.length){
    box.innerHTML = '<div class="mobiles-empty">No test numbers yet.<br>Use + to add one.</div>';
    return;
  }
  const query = (state.mobileFilter || '').trim().toLowerCase();
  // Favourites first, then whatever is waiting on a reply, then by name. A
  // starred number outranks unread deliberately: once autocreate has filled the
  // roster, the handful you actually test with must stay at the top whether or
  // not a stranger's chat happens to be unread right now.
  const ordered = state.users.filter(u => mobileMatches(u, query)).sort((a,b)=>
    Number(!!b.starred)-Number(!!a.starred)
    || unreadFor(b.wa_id)-unreadFor(a.wa_id)
    || String(a.display_name||'').localeCompare(String(b.display_name||'')));
  if(!ordered.length){
    box.innerHTML = `<div class="mobiles-empty">No number matches “${esc(query)}”.</div>`;
    return;
  }
  box.innerHTML = ordered.map(u=>{
    const count = unreadFor(u.wa_id);
    const mine = u.wa_id===state.wa;
    const starred = !!u.starred;
    return `<div class="mobile-row ${count?'unread':''} ${mine?'current':''} ${starred?'starred':''}" data-open-wa="${esc(u.wa_id)}"
        title="${esc(u.display_name||u.wa_id)} · +${esc(u.wa_id)}">
      <div class="mobile-av" style="background:${esc(u.color||'#6a7175')}">${esc(initials(u.display_name||u.wa_id))}</div>
      <div class="mobile-main">
        <div class="mobile-name">${esc(u.display_name||u.wa_id)}${u.auto_created?'<span class="tag-auto">auto</span>':''}</div>
        <div class="mobile-num">+${esc(u.wa_id)}${mine?' · this tab':''}</div>
      </div>
      <button type="button" class="mobile-star ${starred?'on':''}" data-star-wa="${esc(u.wa_id)}"
              aria-pressed="${starred}" title="${starred?'Remove from favourites':'Pin to favourites'}"
              aria-label="${starred?'Remove from favourites':'Pin to favourites'}"><svg><use href="#p-star"/></svg></button>
      <button type="button" class="mobile-edit" data-edit-wa="${esc(u.wa_id)}"
              title="Edit ${esc(u.display_name||u.wa_id)}" aria-label="Edit ${esc(u.display_name||u.wa_id)}"><svg><use href="#p-edit"/></svg></button>
      ${count?`<span class="badge-unread">${count>99?'99+':count}</span>`:''}
    </div>`;
  }).join('');
}

/* ---- add / edit a test number, without a trip to the console ---- */
// null means "creating"; a wa_id means "editing that customer".
let sheetEditing = null;

function openMobileSheet(wa){
  const user = wa ? state.users.find(u=>u.wa_id===wa) : null;
  sheetEditing = user ? user.wa_id : null;
  $('#mobile-sheet-title').textContent = user ? 'Edit test number' : 'Add a test number';
  $('#mobile-wa').value = user ? user.wa_id : '';
  // The wa_id is the primary key and the socket address, so renaming a number
  // would be a delete plus a create, not an edit. Editing changes the label.
  $('#mobile-wa').readOnly = !!user;
  $('#mobile-name').value = user ? (user.display_name||'') : '';
  $('#mobile-color').value = /^#[0-9a-fA-F]{6}$/.test(user?.color||'') ? user.color : '#25D366';
  $('#mobile-delete').hidden = !user;
  $('#mobile-error').hidden = true;
  $('#mobile-sheet').hidden = false;
  setTimeout(()=>$(user ? '#mobile-name' : '#mobile-wa').focus(), 0);
}

function closeMobileSheet(){ $('#mobile-sheet').hidden = true; sheetEditing = null; }

function sheetError(message){
  const box = $('#mobile-error');
  box.textContent = message; box.hidden = false;
}

async function saveMobileSheet(event){
  event.preventDefault();
  const digits = $('#mobile-wa').value.replace(/[^\d]/g,'');
  const name = $('#mobile-name').value.trim();
  const color = $('#mobile-color').value;
  if(!sheetEditing && digits.length < 6){ sheetError('Enter a phone number in full international form, digits only.'); return; }
  const save = $('#mobile-save');
  save.disabled = true;
  try{
    if(sheetEditing){
      await req('/_sandbox/phones/'+encodeURIComponent(sheetEditing), {
        method:'PATCH', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({display_name:name || undefined, color}),
      });
      toast('Number updated');
    }else{
      await req('/_sandbox/phones', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({wa_id:digits, display_name:name || undefined, color}),
      });
      toast('Number added');
    }
    closeMobileSheet();
    // The observer announces the change too, but this tab should not wait on a
    // round trip through the socket to show what it just did.
    await refreshRoster();
  }catch(e){ sheetError(e.message || 'Could not save this number'); }
  finally{ save.disabled = false; }
}

async function deleteMobileFromSheet(){
  if(!sheetEditing) return;
  const user = state.users.find(u=>u.wa_id===sheetEditing);
  const label = user ? (user.display_name||user.wa_id) : sheetEditing;
  if(!confirm(`Delete ${label} (+${sheetEditing})?\nThis removes the number and its whole chat history.`)) return;
  const target = sheetEditing;
  try{
    await req('/_sandbox/phones/'+encodeURIComponent(target), {method:'DELETE'});
    closeMobileSheet();
    toast('Number deleted');
    // Deleting the mobile this tab is acting as leaves nothing to act as, so
    // the simulator goes back to the console rather than to a dead socket.
    if(target === state.wa){ location.href = '/console#simulator'; return; }
    await refreshRoster();
  }catch(e){ sheetError(e.message || 'Could not delete this number'); }
}

async function refreshRoster(){
  try{
    const users = await req('/_sandbox/phones');
    state.users = users.data;
  }catch{ return; }
  await loadUnread();
  renderMobiles();
  renderChatList();
  renderFeed();
  applyMeAvatar(state.users.find(u=>u.wa_id===state.wa));
}

$('#mobile-add').addEventListener('click', ()=>openMobileSheet(null));
$('#mobile-form').addEventListener('submit', saveMobileSheet);
$('#mobile-cancel').addEventListener('click', closeMobileSheet);
$('#mobile-close').addEventListener('click', closeMobileSheet);
$('#mobile-delete').addEventListener('click', deleteMobileFromSheet);
$('#mobile-sheet').addEventListener('click', event=>{ if(event.target.id==='mobile-sheet') closeMobileSheet(); });
$('#mobile-search').addEventListener('input', event=>{ state.mobileFilter = event.target.value; renderMobiles(); });
document.addEventListener('keydown', event=>{ if(event.key==='Escape' && !$('#mobile-sheet').hidden) closeMobileSheet(); });

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

/* Favourite a number. Stored on the customer rather than in localStorage, so
   the same numbers sit at the top in every tab and survive a reload. */
async function toggleStar(wa){
  const user = state.users.find(u => u.wa_id === wa);
  if(!user) return;
  const next = !user.starred;
  // Optimistic: the row redraws immediately and rolls back only if the write
  // fails, because a star that lags a round trip feels broken.
  user.starred = next;
  renderMobiles();
  try{
    await req('/_sandbox/phones/'+encodeURIComponent(wa), {
      method:'PATCH', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({starred: next}),
    });
  }catch(e){
    user.starred = !next;
    renderMobiles();
    toast('Could not update favourites', true);
  }
}

document.addEventListener('click', event=>{
  // These buttons sit inside the row, so they have to be claimed before the
  // row's own handler turns the click into "open this mobile in a tab".
  const star = event.target.closest('[data-star-wa]');
  if(star){ event.stopPropagation(); toggleStar(star.dataset.starWa); return; }
  const edit = event.target.closest('[data-edit-wa]');
  if(edit){ event.stopPropagation(); openMobileSheet(edit.dataset.editWa); return; }
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
  if(data.event==='chat_cleared'){
    // Another tab (or another mobile) cleared something. Only the rows that
    // belonged to that mobile are dropped from this tab's feed.
    state.feed = state.feed.filter(item => item.wa !== data.wa_id
      || (data.phone_number_id && item.phoneId !== data.phone_number_id));
    renderFeed();
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
      const normalized = message.id ? {
        id: message.id,
        direction: data.direction || message.direction || (message.from ? 'inbound' : 'outbound'),
        message_type: message.message_type || message.type,
        payload: message.payload || message,
        status: message.status || 'delivered',
        created_at: message.created_at
          || (message.timestamp ? new Date(Number(message.timestamp)*1000).toISOString() : new Date().toISOString()),
        sender_id: message.sender_id, recipient_id: message.recipient_id,
      } : message;
      appendMessage(normalized);
      if(isUnknownTemplate(normalized)){
        loadTemplateDefinitions().then(()=>renderMessages({scroll:'bottom'}));
      }
      // Arriving while the chat is open means it has been seen. loadMessages()
      // used to do this as a side effect of its full reload; appending has to
      // do it explicitly, or the ticks never reach "read" and the badge sticks.
      markRead([...state.messages.values()]);
      return;
    }
    if(data.event==='status' && data.message_id){ applyStatus(data.message_id, data.status); return; }
    // This mobile's transcript was cleared, possibly from one of its other
    // open tabs, so the open conversation has to drop what it is showing.
    if(data.event==='chat_cleared'){ applyChatCleared(data.phone_number_id || null); return; }
    if(data.event==='read'){
      // Read receipts move every outstanding message, so every cached
      // diagnostic for this chat describes the state before the receipt.
      diagnosticsCache.clear();
      loadMessages();
    }
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
