'use strict';
const cfg = window.BOOKING_CONFIG;
const $ = id => document.getElementById(id);
let allSlots = [], selected = null, admin = false, bookingKey = null, slotKey = null;
let lastBookingPayload = '', lastSlotPayload = '';
const fmt = (value, options) => new Intl.DateTimeFormat('de-DE', {timeZone:'Europe/Berlin', ...options}).format(new Date(value));
const dateLabel = s => fmt(s.start, {weekday:'short',day:'2-digit',month:'long',year:'numeric'});
const times = s => `${fmt(s.start,{hour:'2-digit',minute:'2-digit'})} – ${fmt(s.end,{hour:'2-digit',minute:'2-digit'})} Uhr`;
function node(tag, content, className) {const e=document.createElement(tag);if(content!==undefined)e.textContent=content;if(className)e.className=className;return e;}
function notice(message) {$('notice').textContent=message;}
async function api(path, options={}) {
  const headers = {'Content-Type':'application/json', ...options.headers};
  if(path.startsWith('/admin')) headers.Authorization='Bearer '+(sessionStorage.getItem('access_token')||'');
  const r=await fetch(cfg.apiUrl+path,{...options,headers});
  const data=await r.json();
  if(!r.ok) throw new Error(data.error || (r.status===401?'Bitte erneut anmelden.':'Die Anfrage ist fehlgeschlagen.'));
  return data;
}
const base64url = bytes => btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
async function login() {
  if(!cfg.clientId || !cfg.cognitoDomain) return notice('Der Team-Login muss nach dem AWS-Deployment konfiguriert werden.');
  const verifier=base64url(crypto.getRandomValues(new Uint8Array(32)));
  const state=crypto.randomUUID();
  sessionStorage.setItem('pkce',verifier);sessionStorage.setItem('oauth_state',state);
  const challenge=base64url(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))));
  const params=new URLSearchParams({client_id:cfg.clientId,response_type:'code',scope:'openid email',redirect_uri:location.origin+'/',state,code_challenge:challenge,code_challenge_method:'S256'});
  location.assign(cfg.cognitoDomain+'/oauth2/authorize?'+params);
}
async function callback() {
  const params=new URLSearchParams(location.search);
  if(params.has('error')) {history.replaceState({},'',location.pathname);throw new Error('Anmeldung wurde abgebrochen.');}
  if(!params.has('code')) return;
  const state=sessionStorage.getItem('oauth_state'), verifier=sessionStorage.getItem('pkce');
  history.replaceState({},'',location.pathname);
  sessionStorage.removeItem('oauth_state');sessionStorage.removeItem('pkce');
  if(!state || state!==params.get('state') || !verifier) throw new Error('Ungültige Anmeldung. Bitte erneut anmelden.');
  const r=await fetch(cfg.cognitoDomain+'/oauth2/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'authorization_code',client_id:cfg.clientId,code:params.get('code'),redirect_uri:location.origin+'/',code_verifier:verifier})});
  if(!r.ok) throw new Error('Anmeldung fehlgeschlagen.');
  const data=await r.json(); sessionStorage.setItem('access_token',data.access_token);
}
function renderSlots() {
  const container=$('slots');container.replaceChildren();
  const list=allSlots.filter(s=>!s.closed && new Date(s.start)>new Date())
    .filter(s=>($('kind').value==='all'||s.kind===$('kind').value)&&(!$('month').value||new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Berlin',year:'numeric',month:'2-digit'}).format(new Date(s.start))===$('month').value));
  if(!list.length) container.append(node('p','Für diesen Zeitraum sind noch keine Termine veröffentlicht. Schau bald wieder vorbei.'));
  for(const s of list){
    const card=node('article',undefined,'slot');
    card.append(node('span',s.kind==='event'?'BESONDERES EVENT':'KERAMIKMALEN','badge'),node('span',dateLabel(s),'date'),node('h3',s.title),node('p',times(s)));
    const button=node('button',(s.available??s.remaining>0)?'Diesen Termin buchen ↗':'Bereits ausgebucht');button.disabled=!(s.available??s.remaining>0);button.onclick=()=>openBooking(s,false);card.append(button);container.append(card);
  }
}
async function loadSlots(){
  allSlots=await api(admin?'/admin/slots':'/slots');renderSlots();
  if(admin){
    const picker=$('manage-slot'), previous=picker.value;picker.replaceChildren(new Option('Termin auswählen',''));
    for(const s of allSlots) picker.add(new Option(`${dateLabel(s)} · ${times(s)} · ${s.title} (${s.capacity-s.remaining}/${s.capacity})${s.closed?' · geschlossen':''}`,s.id));
    picker.value=previous;await loadBookings();
  }
}
function openBooking(slot, manual){
  selected={...slot,manual};bookingKey=crypto.randomUUID();lastBookingPayload='';$('booking-form').reset();$('booking-error').textContent='';
  $('booking-title').textContent=slot.title;$('booking-date').textContent=`${dateLabel(slot)} · ${times(slot)}`;
  // Administrative bookings do not require impersonating customer consent.
  const consent=$('booking-form').querySelector('[type=checkbox]');consent.required=!manual;consent.closest('label').hidden=manual;
  $('booking-dialog').showModal();
}
async function loadBookings(){
  const id=$('manage-slot').value, slot=allSlots.find(s=>s.id===id);$('bookings').replaceChildren();
  $('close-slot').disabled=!slot;$('manual-book').disabled=!slot||slot.closed||slot.remaining<1;
  if(!slot)return;
  $('close-slot').textContent=slot.closed?'Buchung wieder öffnen':'Für neue Buchungen schließen';
  const list=await api('/admin/bookings?slotId='+encodeURIComponent(id));
  if($('manage-slot').value!==id)return;
  if(!list.length)$('bookings').append(node('p','Noch keine Buchungen.'));
  for(const b of list){
    const row=node('article',undefined,'booking-row');row.append(node('strong',`${b.name} · ${b.people} Personen`),node('p',`${b.email} · ${b.phone}`),node('p',b.status==='cancelled'?'Storniert':'Bestätigt'));
    if(b.status==='confirmed'){
      const button=node('button','Stornieren','quiet');button.onclick=async()=>{
        if(!confirm(`Buchung für ${b.name} (${b.people} Personen) stornieren?`))return;
        button.disabled=true;
        try{await api(`/admin/bookings/${b.id}/cancel`,{method:'POST',body:'{}'});await loadSlots();notice('Buchung storniert. Die Plätze sind wieder verfügbar.');}catch(e){notice(e.message);button.disabled=false;}
      };row.append(button);
    }$('bookings').append(row);
  }
}
$('booking-form').onsubmit=async e=>{
  e.preventDefault();const data=Object.fromEntries(new FormData(e.target));data.people=Number(data.people);data.slotId=selected.id;
  const payload=JSON.stringify(data);if(lastBookingPayload&&lastBookingPayload!==payload)bookingKey=crypto.randomUUID();lastBookingPayload=payload;
  $('booking-submit').disabled=true;$('booking-error').textContent='';
  try{const result=await api(selected.manual?'/admin/bookings':'/bookings',{method:'POST',headers:{'Idempotency-Key':bookingKey},body:payload});$('booking-dialog').close();notice(`Gebucht! Deine Buchungsnummer: ${result.id}. Die E-Mail-Bestätigung folgt in Kürze.`);await loadSlots();}catch(err){$('booking-error').textContent=err.message;}finally{$('booking-submit').disabled=false;}
};
$('slot-form').onsubmit=async e=>{
  e.preventDefault();const data=Object.fromEntries(new FormData(e.target));data.capacity=Number(data.capacity);data.duration=Number(data.duration);
  data.weekdays=[...$('weekdays').querySelectorAll(':checked')].map(x=>Number(x.value));data.excludedDates=data.excludedDates.split(/\s+/).filter(Boolean);
  const payload=JSON.stringify(data);if(payload!==lastSlotPayload){slotKey=crypto.randomUUID();lastSlotPayload=payload;}
  const button=e.target.querySelector('button');button.disabled=true;
  try{const result=await api('/admin/slots',{method:'POST',headers:{'Idempotency-Key':slotKey},body:payload});notice(`${result.ids.length} Termine veröffentlicht.`);await loadSlots();}catch(err){notice(err.message);}finally{button.disabled=false;}
};
$('close-slot').onclick=async()=>{const slot=allSlots.find(s=>s.id===$('manage-slot').value);if(!slot)return;try{await api('/admin/slots/'+slot.id,{method:'PATCH',body:JSON.stringify({closed:!slot.closed})});await loadSlots();}catch(e){notice(e.message);}};
$('manual-book').onclick=()=>openBooking(allSlots.find(s=>s.id===$('manage-slot').value),true);
$('manage-slot').onchange=()=>loadBookings().catch(e=>notice(e.message));
$('admin-login').onclick=()=>login().catch(e=>notice(e.message));
$('logout').onclick=()=>{sessionStorage.removeItem('access_token');location.assign(cfg.cognitoDomain+'/logout?'+new URLSearchParams({client_id:cfg.clientId,logout_uri:location.origin+'/'}));};
$('dialog-close').onclick=()=>$('booking-dialog').close();$('month').onchange=renderSlots;$('kind').onchange=renderSlots;
['Mo','Di','Mi','Do','Fr','Sa','So'].forEach((day,i)=>{const label=node('label');const input=node('input');input.type='checkbox';input.value=i;input.checked=true;label.append(input,document.createTextNode(day));$('weekdays').append(label);});
(async()=>{
  if(!cfg.apiUrl){notice('Die Oberfläche ist bereit. Für echte Buchungen muss zuerst das AWS-Backend eingerichtet werden.');$('slots').replaceChildren(node('p','Noch nicht mit dem Buchungssystem verbunden.'));return;}
  try{await callback();if(sessionStorage.getItem('access_token')){try{await api('/admin/slots');admin=true;}catch(e){sessionStorage.removeItem('access_token');notice(e.message);}}
    $('admin').hidden=!admin;$('logout').hidden=!admin;$('admin-login').hidden=admin;await loadSlots();
  }catch(e){notice(e.message);$('slots').replaceChildren(node('p','Termine konnten nicht geladen werden. Bitte lade die Seite erneut.'));}
})();
