import {makeScenario,scenarioIds} from './fixtures.mjs';
import {createFixtureClient} from './fixture-client.mjs';import {createController} from './controller.mjs';
import {renderShell} from './shell.mjs';import {renderCounts} from './counts.mjs';
import {createEvidenceView,supportingSightings} from './evidence.mjs';import {el,button,meta} from './dom.mjs';
const root=document.getElementById('app');let unsubscribe=()=>{},currentIndex=0,scenario,client,controller,evidence=null,selection=null,lastVersion=null;
function select(kind,id){selection={kind,id};updateSelection(controller.getState().survey);}
function updateSelection(s){if(!s||!selection)return;const ids=selection.kind==='frame'?[selection.id]:supportingSightings(s,selection).map(x=>x.frame_id);for(const b of root.querySelectorAll('[data-frame-id]'))b.setAttribute('aria-pressed',String(ids.includes(b.dataset.frameId)));evidence.select(s,selection);}
const handlers={onFrame:id=>select('frame',id),onClass:id=>select('class',id),onObject:id=>select('object',id)};
function controls(){const host=document.getElementById('reviewer-controls');if(!host.querySelector('#scenario')){
 const select=el('select',{id:'scenario'},scenarioIds.map(id=>el('option',{value:id},[makeScenario(id).title])));select.addEventListener('change',()=>open(select.value));
 host.append(el('div',{class:'section-label'},['REVIEWER CONTROLS']),el('label',{for:'scenario'},['Fixture scenario']),select,button('Next fixture snapshot',async()=>{client.advance();currentIndex++;await controller.refresh();controls();},{id:'next-snapshot'}),meta('Advances supplied snapshots only. No drone or provider connection.'));
 }host.querySelector('#scenario').value=scenario.id;host.querySelector('#next-snapshot').disabled=currentIndex>=scenario.snapshots.length-1;}
async function open(id){
 unsubscribe();evidence?.dispose();evidence=null;selection=null;lastVersion=null;scenario=makeScenario(id);currentIndex=0;client=createFixtureClient(scenario);controller=createController(client);
 unsubscribe=controller.subscribe(state=>{renderShell(root,state,handlers);if(!evidence)evidence=createEvidenceView(document.getElementById('evidence'),client);if(state.survey){renderCounts(document.getElementById('counts'),state.survey,handlers);if(lastVersion!==state.survey.version){lastVersion=state.survey.version;updateSelection(state.survey);}else if(selection){const ids=selection.kind==='frame'?[selection.id]:supportingSightings(state.survey,selection).map(x=>x.frame_id);for(const b of root.querySelectorAll('[data-frame-id]'))b.setAttribute('aria-pressed',String(ids.includes(b.dataset.frameId)));}}controls();});
 await controller.open(scenario.snapshots[0].run_id);
}
await open('computers-followup');
