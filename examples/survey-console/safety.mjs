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
 const identity=()=>{const id=crypto.randomUUID();return {command_id:id,correlation_id:id,idempotency_key:id};};
 const landController=providedLandController||createControlledLandController({getSnapshot,requestLand:client.request_land,identity,onStatus:text=>{status.textContent=text;}});
 const unsubscribeLand=landController.subscribe?.(text=>{status.textContent=text;});
 async function land(){await landController.request();}
 const landButton=button('Emergency land',land,{id:'land-now',class:'emergency-control',disabled:true,'aria-describedby':'safety-status',title:'Request a controlled landing'});
 host.append(landButton,status);
 return {landController,render(s){
  landButton.disabled=landController.isPending()||!isLandLegal(s);
  if(!isLandLegal(s))status.textContent='Landing available during an active flight';
 },dispose(){unsubscribeLand?.();}};
}
