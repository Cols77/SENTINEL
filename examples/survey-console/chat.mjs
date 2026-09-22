import {el,button,replace} from './dom.mjs';
export function terminalChatResponse(status){return {COMPLETE:'COMPLETE - run is complete; no new dispatch is available.',ABORTED:'ABORTED - run was aborted; no new dispatch is available.',RECOVERY_REQUIRED:'RECOVERY_REQUIRED - reconcile the aircraft before dispatch.'}[status]||null;}
export function createChatView(host,client,getSnapshot){
 const transcript=el('div',{class:'mission-transcript',role:'log','aria-label':'Mission conversation'});
 const input=el('textarea',{id:'mission-request','aria-label':'Mission request',rows:3,maxlength:4000,placeholder:'Ask to count the computers…'});
 const status=el('p',{role:'status',class:'meta'});
 const send=button('Send',()=>submit(input.value),{id:'send-chat'});
 const form=el('form',{},[el('label',{for:'mission-request'},['Mission request']),input,send,status]);
 form.addEventListener('submit',e=>{e.preventDefault();submit(input.value);});
 host.append(transcript,form);
 let pending=false,attempt=null,last='',disposed=false;
 async function submit(text){
  if(pending||disposed||!text.trim())return;
  const s=getSnapshot();if(!s)return;
  const terminal=terminalChatResponse(s.run.status);if(terminal){status.textContent=terminal;return;}
  if(!attempt||attempt.text!==text){const id=crypto.randomUUID();attempt={session_id:s.session.id,run_id:s.run.id,command_id:id,correlation_id:id,idempotency_key:id,expected_version:s.run.decision_version,text};}
  pending=true;send.disabled=true;status.textContent='Sending…';
  try{const result=await client.submit_chat(s.run.id,attempt);if(result.status==='ACKNOWLEDGED'){input.value='';attempt=null;}else if(result.status!=='IN_DOUBT'){attempt=null;}status.textContent=result.message;}
  catch{status.textContent='Connection interrupted. Your text is preserved; Send retries the same request.';}
  finally{pending=false;send.disabled=false;}
 }
 return {submit,render(messages){
  const key=JSON.stringify(messages);if(key===last)return;last=key;
  const atEnd=transcript.scrollHeight-transcript.scrollTop-transcript.clientHeight<40;
  replace(transcript,messages.map(m=>el('article',{class:`message message-${m.role.toLowerCase()}`},[el('strong',{},[m.role.toLowerCase()]),el('p',{},[m.body])])));
  if(atEnd)transcript.scrollTop=transcript.scrollHeight;
 },dispose(){disposed=true;}};
}
