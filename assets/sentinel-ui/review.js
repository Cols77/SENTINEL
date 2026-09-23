'use strict';
// All interactions are local illustrations. There are no network requests.
const $=id=>document.getElementById(id);
const labels={UNSURVEYED:'Not surveyed',SURVEYING:'Surveying',SURVEYED:'Surveyed',HAZARD_PENDING:'Hazard pending',BLOCKED:'Blocked'};
const statuses=new Map();let selected=null;
function zones(){return [...$('map-host').querySelectorAll('.survey-zone')]}
function renderText(){
  $('map-description').replaceChildren(...zones().map(z=>{const row=document.createElement('div');const title=document.createElement('span');title.textContent=z.dataset.label;const state=document.createElement('span');state.textContent=labels[z.dataset.status];row.append(title,state);return row}));
}
function selectZone(zone){
  selected=zone.id;
  zones().forEach(z=>{const active=z.id===selected;z.classList.toggle('is-selected',active);z.setAttribute('aria-pressed',String(active))});
  $('zone-state').value=zone.dataset.status;
  $('selection-readout').textContent=zone.id+' · '+labels[zone.dataset.status]+' · illustrative selection';
}
function bindMap(){
  zones().forEach(z=>{const status=statuses.get(z.id)||'UNSURVEYED';z.dataset.status=status;z.querySelector('.zone-status').textContent=labels[status];z.setAttribute('aria-label',z.dataset.label+': '+labels[status]);z.addEventListener('click',()=>selectZone(z));z.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectZone(z)}})});
  const prior=zones().find(z=>z.id===selected);if(prior)selectZone(prior);else {selected=null;$('selection-readout').textContent='Select a zone by click, Enter or Space.'}
  $('zone-state').disabled=!selected;renderText();
}
$('map-host').addEventListener('click',()=>{$('zone-state').disabled=!selected});
$('map-host').addEventListener('keydown',()=>{$('zone-state').disabled=!selected});
$('layout-mode').addEventListener('change',()=>{const route=$('layout-mode').value==='route';$('map-host').replaceChildren($(route?'route-map-template':'marker-map-template').content.cloneNode(true));$('map-title').textContent=route?'Route-only · not visually verified':'Marker mode';bindMap()});
$('zone-state').addEventListener('change',()=>{if(!selected)return;const z=zones().find(x=>x.id===selected);const status=$('zone-state').value;statuses.set(selected,status);z.dataset.status=status;z.querySelector('.zone-status').textContent=labels[status];z.setAttribute('aria-label',z.dataset.label+': '+labels[status]);selectZone(z);renderText()});
$('stale-toggle').addEventListener('change',()=>{const stale=$('stale-toggle').checked;$('freshness-badge').textContent=stale?'STALE · last known snapshot':'FRESH';$('freshness-badge').className='badge '+(stale?'warning':'good')});
$('marker-toggle').addEventListener('change',()=>{$('marker-example').hidden=!$('marker-toggle').checked;$('camera-stage').classList.toggle('has-marker',$('marker-toggle').checked)});
$('degraded-toggle').addEventListener('change',()=>{$('degraded-example').hidden=!$('degraded-toggle').checked});
$('preflight-demo').addEventListener('change',()=>{const mode=$('preflight-demo').value;document.querySelectorAll('.preflight-list li').forEach((li,i)=>{const failed=mode==='failed'&&i===3;li.classList.toggle('failed',failed);li.querySelector('strong').textContent=mode==='unknown'?'Unknown':failed?'Failed':i===7?'Passed · latch clear':'Passed';const glyph=document.querySelector('a[href="icons/'+(failed?'warning':'approve')+'.svg"] svg').cloneNode(true);glyph.setAttribute('aria-hidden','true');li.querySelector('svg').replaceWith(glyph);glyph.style.visibility=mode==='unknown'?'hidden':'visible'});$('force-example').hidden=mode!=='failed';$('preflight-message').textContent=mode==='passed'?'Illustrative report passed. Dispatch still requires coordinator eligibility.':mode==='failed'?'Illustrative LIVE_VIDEO check failed. A new run requires a legal operator action and reason.':'Report unavailable. Dispatch eligibility is unknown.'});
function emergency(){ $('ordinary-demo').disabled=true;$('ack-demo').hidden=false;$('emergency-demo-status').classList.add('active');$('emergency-demo-status').textContent='Stop request pending · local illustration. No physical outcome is implied.'; }
$('emergency-demo-button').addEventListener('click',emergency);
$('ack-demo').addEventListener('click',()=>{$('emergency-demo-status').textContent='EMERGENCY LATCH ACKNOWLEDGED · illustrative coordinator response. Ordinary controls remain disabled.';$('ack-demo').hidden=true});
$('reset-emergency').addEventListener('click',()=>{$('ordinary-demo').disabled=false;$('ack-demo').hidden=true;$('emergency-demo-status').classList.remove('active');$('emergency-demo-status').textContent='Ordinary presentation · Shift+Escape previews a stop request.'});
$('ordinary-demo').addEventListener('click',()=>{$('emergency-demo-status').textContent='Local button preview only. No coordinator command was submitted.'});
document.addEventListener('keydown',e=>{if(e.shiftKey&&e.key==='Escape'){e.preventDefault();emergency();$('emergency-demo-status').scrollIntoView({block:'center'})}});
bindMap();
