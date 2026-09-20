'use strict';
function previewEmergency(){document.body.classList.add('emergency-mode');const notice=document.getElementById('console-notice');notice.classList.add('emergency');notice.textContent='STOP REQUEST PENDING · Local composition preview only. No command sent; no landing or latch acknowledgement inferred.';}
document.getElementById('console-emergency').addEventListener('click',previewEmergency);
document.addEventListener('keydown',e=>{if(e.shiftKey&&e.key==='Escape'){e.preventDefault();previewEmergency()}});
// This composition uses a static schematic; remove interactive semantics.
document.querySelectorAll('.survey-zone').forEach(z=>{z.removeAttribute('tabindex');z.removeAttribute('aria-pressed');z.setAttribute('role','group')});
