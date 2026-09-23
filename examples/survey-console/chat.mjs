import {el,button,replace} from './dom.mjs';
import {createVoiceView} from './voice.mjs';
export function terminalChatResponse(status){return {COMPLETE:'COMPLETE - run is complete; no new dispatch is available.',ABORTED:'ABORTED - run was aborted; no new dispatch is available.',RECOVERY_REQUIRED:'RECOVERY_REQUIRED - reconcile the aircraft before dispatch.'}[status]||null;}
const makeIdentity=()=>{const id=crypto.randomUUID();return {session_id:null,run_id:null,command_id:id,correlation_id:id,idempotency_key:id};};
export function createChatSubmissionController({client,getSnapshot,onStatus=()=>{},onPending=()=>{},setInput=()=>{},identity=makeIdentity}={}){
 let pending=false,attempt=null,disposed=false;
 async function submit(text){
  if(pending||disposed||!String(text).trim())return;
  const snapshot=getSnapshot();if(!snapshot)return;
  const terminal=terminalChatResponse(snapshot.run.status);if(terminal){onStatus(terminal);return;}
  if(!attempt||attempt.text!==text){const base=identity();attempt={...base,session_id:snapshot.session.id,run_id:snapshot.run.id,expected_version:snapshot.run.decision_version,text};}
  pending=true;onPending(true);onStatus('Sending…');
  try{const result=await client.submit_chat(attempt.run_id,attempt);if(result.status==='ACKNOWLEDGED'){setInput('');attempt=null;}else if(result.status!=='IN_DOUBT'){attempt=null;}onStatus(result.message);return result;}
  catch{onStatus('Connection interrupted. Your text is preserved; Send retries the same request.');}
  finally{pending=false;onPending(false);}
 }
 return {submit,getAttempt:()=>attempt,isPending:()=>pending,dispose:()=>{disposed=true;}};
}
export function createChatView(host,client,getSnapshot,{landController=null,voiceOptions={}}={}){
 const transcript=el('div',{class:'mission-transcript',role:'log','aria-label':'Mission conversation'});
 const input=el('textarea',{id:'mission-request','aria-label':'Mission request',rows:3,maxlength:4000,placeholder:'Ask to count the computers…'});
 const status=el('p',{role:'status',class:'meta'});
 const send=button('Send',()=>submit(input.value),{id:'send-chat'});
 const voiceHost=el('div',{class:'voice-composer'});
 const form=el('form',{},[el('label',{for:'mission-request'},['Mission request']),input,el('div',{class:'composer-actions'},[send,voiceHost]),status,el('p',{class:'meta voice-privacy'},['Hold to talk uses the browser speech service; audio may be processed remotely. Audio is not uploaded or stored.'])]);
 form.addEventListener('submit',e=>{e.preventDefault();submit(input.value);});
 host.append(transcript,form);
 let last='';
 const controller=createChatSubmissionController({client,getSnapshot,onStatus:text=>{status.textContent=text;},onPending:value=>{send.disabled=value;},setInput:text=>{input.value=text;}});
 const voice=landController?createVoiceView(voiceHost,input,getSnapshot,controller,landController,voiceOptions):null;
 function submit(text){return controller.submit(text);}
 return {submit,render(messages){
  const key=JSON.stringify(messages);if(key===last)return;last=key;
  const atEnd=transcript.scrollHeight-transcript.scrollTop-transcript.clientHeight<40;
  replace(transcript,messages.map(m=>el('article',{class:`message message-${m.role.toLowerCase()}`},[el('strong',{},[m.role.toLowerCase()]),el('p',{},[m.body])])));
  if(atEnd)transcript.scrollTop=transcript.scrollHeight;
 },dispose(){controller.dispose();voice?.dispose();}};
}
