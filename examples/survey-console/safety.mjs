import {el,button} from './dom.mjs';
export function createHoldAction(send,{schedule=setTimeout,cancel=clearTimeout}={}){
 let timer=null,holding=false;
 return {begin(){if(holding)return;holding=true;timer=schedule(()=>{timer=null;holding=false;send();},1200);},cancel(){if(timer!==null)cancel(timer);timer=null;holding=false;}};
}
export function createSafetyView(host,client,getSnapshot){
 const status=el('span',{role:'status','aria-live':'polite',class:'safety-status'});
 let pending=false,emergencyAttempt=null,landAttempt=null;
 const identity=()=>{const id=crypto.randomUUID();return {command_id:id,correlation_id:id,idempotency_key:id};};
 async function land(){
  const s=getSnapshot();if(!s||pending)return;pending=true;landButton.disabled=true;
  landAttempt??=identity();status.textContent='Land requested';
  try{const result=await client.request_land(s.run.id,landAttempt);status.textContent=`Land: ${result.status}`;if(result.status!=='IN_DOUBT')landAttempt=null;}
  catch{status.textContent='Land outcome unknown; retry preserves command identity.';}
  finally{pending=false;}
 }
 async function emergency(){
  const s=getSnapshot();if(!s||pending)return;pending=true;emergencyButton.disabled=true;
  emergencyAttempt??=identity();status.textContent='Emergency requested';
  try{const result=await client.trigger_emergency({session_id:s.session.id,identity:emergencyAttempt,active_run_id:s.run.id});status.textContent=`Emergency: ${result.status} · ${s.session.mode==='REHEARSAL'?'simulated transport':'command acceptance only'}`;if(result.status!=='IN_DOUBT')emergencyAttempt=null;}
  catch{status.textContent='Emergency outcome unknown. Use the terminal or physical fallback.';}
  finally{pending=false;}
 }
 const landButton=button('Land now',land,{id:'land-now',disabled:true});
 const emergencyButton=button('Hold to stop motors',null,{id:'emergency-stop',class:'emergency-control',disabled:true,'aria-label':'Emergency motor stop: hold for 1.2 seconds'});
 const hold=createHoldAction(emergency);
 emergencyButton.addEventListener('pointerdown',e=>{if(e.button!==0)return;emergencyButton.setPointerCapture(e.pointerId);hold.begin();});
 for(const event of ['pointerup','pointercancel','lostpointercapture','blur'])emergencyButton.addEventListener(event,()=>hold.cancel());
 emergencyButton.addEventListener('keydown',e=>{if([' ','Enter'].includes(e.key)){e.preventDefault();if(!e.repeat)hold.begin();}});
 emergencyButton.addEventListener('keyup',()=>hold.cancel());
 host.append(landButton,emergencyButton,status);
 return {render(s){landButton.disabled=pending||!s.safety.land.available;emergencyButton.disabled=pending||!s.safety.emergency.available;if(!s.safety.emergency.available)status.textContent='Emergency transport unavailable · use terminal/physical fallback';},dispose(){hold.cancel();}};
}
