import {el,button} from './dom.mjs';

export function isLandLegal(snapshot){
 const action=snapshot?.legal_actions?.find(row=>row.action==='LAND');
 return Boolean(snapshot?.safety?.land?.available&&action?.enabled);
}

export function createControlledLandController({getSnapshot,requestLand,identity=()=>{const id=crypto.randomUUID();return {command_id:id,correlation_id:id,idempotency_key:id};},onStatus=()=>{}}={}){
 let pending=false,attempt=null,settled=false,lastResult=null;const listeners=new Set();
 const notify=text=>{onStatus(text);for(const listener of listeners)listener(text);};
 async function request(){
  if(pending||settled)return lastResult;
  const snapshot=getSnapshot();
  if(!isLandLegal(snapshot)){notify('Controlled Land unavailable: current safety state does not permit LAND.');return {status:'REFUSED'};}
  pending=true;attempt??=identity();notify('Land requested');
  try{lastResult=await requestLand(snapshot.run.id,attempt);notify(`Land: ${lastResult.status}`);if(lastResult.status!=='IN_DOUBT')settled=true;return lastResult;}
  catch(error){notify('Land outcome unknown; retry preserves command identity.');return {status:'IN_DOUBT',error};}
  finally{pending=false;}
 }
 return {request,getAttempt:()=>attempt,getResult:()=>lastResult,isPending:()=>pending,subscribe(listener){listeners.add(listener);return()=>listeners.delete(listener);}};
}

export function createHoldAction(send,{schedule=setTimeout,cancel=clearTimeout}={}){
 let timer=null,holding=false;
 return {begin(){if(holding)return;holding=true;timer=schedule(()=>{timer=null;holding=false;send();},1200);},cancel(){if(timer!==null)cancel(timer);timer=null;holding=false;}};
}

export function createSafetyView(host,client,getSnapshot,{landController:providedLandController}={}){
 const status=el('span',{id:'safety-status',role:'status','aria-live':'polite',class:'safety-status'});
 let emergencyPending=false,emergencyAttempt=null;
 const identity=()=>{const id=crypto.randomUUID();return {command_id:id,correlation_id:id,idempotency_key:id};};
 const landController=providedLandController||createControlledLandController({getSnapshot,requestLand:client.request_land,identity,onStatus:text=>{status.textContent=text;}});
 const unsubscribeLand=landController.subscribe?.(text=>{status.textContent=text;});
 async function land(){await landController.request();}
 async function emergency(){
  const s=getSnapshot();if(!s||emergencyPending)return;emergencyPending=true;emergencyButton.disabled=true;
  emergencyAttempt??=identity();status.textContent='Emergency requested';
  try{const result=await client.trigger_emergency({session_id:s.session.id,identity:emergencyAttempt,active_run_id:s.run.id});status.textContent=`Emergency: ${result.status} · ${s.session.mode==='REHEARSAL'?'simulated transport':'command acceptance only'}`;if(result.status!=='IN_DOUBT')emergencyAttempt=null;}
  catch{status.textContent='Emergency outcome unknown. Use the terminal or physical fallback.';}
  finally{emergencyPending=false;}
 }
 const landButton=button('Land now',land,{id:'land-now',disabled:true,'aria-describedby':'safety-status'});
 const emergencyButton=button('Hold to stop motors',null,{id:'emergency-stop',class:'emergency-control',disabled:true,'aria-label':'Emergency motor stop: hold for 1.2 seconds','aria-describedby':'safety-status'});
 const hold=createHoldAction(emergency);
 emergencyButton.addEventListener('pointerdown',e=>{if(e.button!==0)return;emergencyButton.setPointerCapture(e.pointerId);hold.begin();});
 for(const event of ['pointerup','pointercancel','lostpointercapture','blur'])emergencyButton.addEventListener(event,()=>hold.cancel());
 emergencyButton.addEventListener('keydown',e=>{if([' ','Enter'].includes(e.key)){e.preventDefault();if(!e.repeat)hold.begin();}});
 emergencyButton.addEventListener('keyup',()=>hold.cancel());
 host.append(landButton,emergencyButton,status);
 return {landController,render(s){
  landButton.disabled=landController.isPending()||!isLandLegal(s);
  emergencyButton.disabled=emergencyPending||!s.safety.emergency.available;
  const unavailable=[];
  if(!isLandLegal(s))unavailable.push('Controlled Land unavailable until a flight runner is active');
  if(!s.safety.emergency.available)unavailable.push('Emergency transport unavailable · use terminal or physical fallback');
  if(unavailable.length)status.textContent=unavailable.join('. ');
 },dispose(){hold.cancel();unsubscribeLand?.();}};
}
