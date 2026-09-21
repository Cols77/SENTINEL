import {renderCounts} from './counts.mjs';
import {makeScenario,scenarioIds} from './fixtures.mjs';import {createFixtureClient} from './fixture-client.mjs';import {createController} from './controller.mjs';import {renderShell} from './shell.mjs';import {el,button,meta} from './dom.mjs';
const root=document.getElementById('app');let unsubscribe=()=>{},currentIndex=0,scenario,client,controller;
function controls(){const host=document.getElementById('reviewer-controls');if(!host.querySelector('#scenario')){
 const select=el('select',{id:'scenario'},scenarioIds.map(id=>el('option',{value:id},[makeScenario(id).title])));select.addEventListener('change',()=>open(select.value));
 host.append(el('div',{class:'section-label'},['REVIEWER CONTROLS']),el('label',{for:'scenario'},['Fixture scenario']),select,button('Next fixture snapshot',async()=>{client.advance();currentIndex++;await controller.refresh();controls();},{id:'next-snapshot'}),meta('Advances supplied snapshots only. No drone or provider connection.'));
 }host.querySelector('#scenario').value=scenario.id;host.querySelector('#next-snapshot').disabled=currentIndex>=scenario.snapshots.length-1;}
async function open(id){unsubscribe();scenario=makeScenario(id);currentIndex=0;client=createFixtureClient(scenario);controller=createController(client);unsubscribe=controller.subscribe(state=>{renderShell(root,state);if(state.survey)renderCounts(document.getElementById('counts'),state.survey);controls();});await controller.open(scenario.snapshots[0].run_id);}
await open('computers-followup');
