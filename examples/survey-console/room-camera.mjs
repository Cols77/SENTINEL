import {el,button,replace} from './dom.mjs';

const svgNode=(tag,attrs={},text=null)=>{
 const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
 for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));
 if(text!==null)node.textContent=text;
 return node;
};

function drawTags(svg,tags,width,height){
 svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
 replace(svg,tags.flatMap(tag=>{
  const corners=tag.corners??[];
  if(corners.length!==4||corners.some(point=>point.length!==2||point.some(n=>!Number.isFinite(n))))return [];
  const [x,y]=corners[0];
  return [svgNode('polygon',{points:corners.map(point=>point.join(',')).join(' '),class:'room-overlay-tag'}),
   svgNode('text',{x,y:Math.max(20,y-8),class:'room-overlay-label room-overlay-tag-label'},`TAG ${tag.id}`)];
 }));
}

function drawObjects(svg,detections,width,height){
 svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
 replace(svg,detections.flatMap(item=>{
  const [x,y,w,h]=item.bbox??[];
  if(![x,y,w,h].every(Number.isFinite)||w<=0||h<=0)return [];
  const label=`${item.label} · ${item.confidence}`;
  return [svgNode('rect',{x,y,width:w,height:h,class:`room-overlay-object is-${item.confidence}`}),
   svgNode('text',{x,y:Math.max(20,y-8),class:'room-overlay-label room-overlay-object-label'},label)];
 }));
}

export function createRoomCamera(host,fetchImpl=fetch){
 const source=el('span',{class:'room-camera-source'},['AWAITING CAMERA']);
 const liveButton=button('Live camera',()=>select('live'),{class:'room-camera-tab','aria-pressed':'true'});
 const captureButton=button('Gemini capture',()=>select('capture'),{class:'room-camera-tab','aria-pressed':'false'});
 const reviewButton=button('Recorded review',()=>select('review'),{class:'room-camera-tab','aria-pressed':'false'});
 const liveImage=el('img',{alt:'Drone camera frame',hidden:true});
 const liveOverlay=svgNode('svg',{'aria-hidden':'true',class:'room-camera-overlay'});
 const liveEmpty=el('p',{class:'room-camera-empty'},['Camera starts after dispatch.']);
 const liveStage=el('div',{class:'room-camera-stage','data-testid':'room-camera-live'},[liveImage,liveOverlay,liveEmpty]);
 const liveNote=el('p',{class:'room-camera-note'},['AprilTag outlines follow the current decoded frame.']);
 const captureImage=el('img',{alt:'Saved drone capture',hidden:true});
 const captureOverlay=svgNode('svg',{'aria-hidden':'true',class:'room-camera-overlay'});
 const captureEmpty=el('p',{class:'room-camera-empty'},['No saved capture yet.']);
 const captureStage=el('div',{class:'room-camera-stage','data-testid':'room-camera-capture',hidden:true},[captureImage,captureOverlay,captureEmpty]);
 const captureNote=el('p',{class:'room-camera-note',hidden:true},['Detection boxes belong to this captured frame.']);
 host.append(
  el('div',{class:'room-camera-heading'},[el('div',{},[el('p',{class:'section-label'},['VISUAL FEED']),el('h3',{},['Drone camera'])]),source]),
  el('div',{class:'room-camera-tabs',role:'group','aria-label':'Camera view'},[liveButton,captureButton,reviewButton]),
  liveStage,captureStage,liveNote,captureNote,
 );

 let runId=null,mode=null,running=false,view='live',liveUrl=null,captureUrl=null,reviewPanel=null,reviewState=null;
 let liveAt=null,liveTags=[],captureId=null,captureData=null,liveTimer=null,captureTimer=null;
 let liveBusy=false,captureBusy=false,epoch=0,disposed=false,terminalCaptureAttempts=0;

 function select(next){
  view=next;
  liveButton.setAttribute('aria-pressed',String(view==='live'));
  captureButton.setAttribute('aria-pressed',String(view==='capture'));
  reviewButton.setAttribute('aria-pressed',String(view==='review'));
  liveStage.hidden=view!=='live';captureStage.hidden=view!=='capture';
  liveNote.hidden=view!=='live';captureNote.hidden=view!=='capture';
  if(reviewPanel)reviewPanel.hidden=view!=='review';
  if(view==='capture'&&runId&&!captureData)void loadCapture(epoch);
  if(view==='live'&&running)void loadLive(epoch);
  updateSource();
 }

 function clear(){
  epoch++;clearTimeout(liveTimer);clearTimeout(captureTimer);
  liveTimer=null;captureTimer=null;
  if(liveUrl)URL.revokeObjectURL(liveUrl);
  if(captureUrl)URL.revokeObjectURL(captureUrl);
  liveUrl=null;captureUrl=null;liveAt=null;liveTags=[];captureId=null;captureData=null;
  liveImage.hidden=true;captureImage.hidden=true;
  liveImage.removeAttribute('src');captureImage.removeAttribute('src');
  replace(liveOverlay,[]);replace(captureOverlay,[]);
  liveEmpty.hidden=false;captureEmpty.hidden=false;
  terminalCaptureAttempts=0;
 }

 function updateSource(){
  if(view==='review'){
   source.textContent=reviewState?.analysis?.status??'NO RECORDED REVIEW';
   source.classList.remove('is-stale');
   return;
  }
  if(!runId){source.textContent='AWAITING CAMERA';return;}
  if(view==='capture'){
   source.textContent=captureData?`${captureData.verified?'ANALYZED':'RECORDED'} · ${new Date(captureData.captured_at).toLocaleTimeString()}`:'AWAITING CAPTURE';
   source.classList.remove('is-stale');
   return;
  }
  if(!liveAt){source.textContent=running?'CONNECTING':'NO CAMERA FRAME';return;}
  const age=Math.max(0,Math.round((Date.now()-Date.parse(liveAt))/1000));
  source.textContent=`${mode==='LIVE'?'LIVE':'SIMULATED'} · ${running&&age<=3?'CURRENT':`${age}s old`}`;
  source.classList.toggle('is-stale',!running||age>3);
  liveImage.alt=`${mode==='LIVE'?'Live':'Simulated'} drone camera frame from ${new Date(liveAt).toLocaleTimeString()}; ${liveTags.length} AprilTags visible`;
 }

 async function loadLive(token){
  if(liveBusy||disposed||!running||!runId||view!=='live')return;
  liveBusy=true;
  try{
   const response=await fetchImpl(`/api/v1/room-agent/camera/live/${encodeURIComponent(runId)}`,{cache:'no-store'});
   if(token!==epoch||disposed)return;
   if(response.status===204){liveEmpty.textContent='Waiting for decoded camera frames…';return;}
   if(!response.ok)throw Error('camera unavailable');
   const blob=await response.blob();
   if(token!==epoch||disposed)return;
   const nextUrl=URL.createObjectURL(blob);
   if(liveUrl)URL.revokeObjectURL(liveUrl);
   liveUrl=nextUrl;liveImage.src=nextUrl;liveImage.hidden=false;liveEmpty.hidden=true;
   liveAt=response.headers.get('x-frame-at');
   const width=Number(response.headers.get('x-frame-width'));
   const height=Number(response.headers.get('x-frame-height'));
   liveTags=JSON.parse(response.headers.get('x-apriltags')||'[]');
   if(width>0&&height>0){liveStage.style.aspectRatio=`${width} / ${height}`;drawTags(liveOverlay,liveTags,width,height);}
   liveNote.textContent=liveTags.length?`AprilTags in this frame: ${liveTags.map(tag=>tag.id).join(', ')}.`:'No AprilTags in this frame.';
   updateSource();
  }catch{
   if(token===epoch){liveEmpty.textContent='Camera frame unavailable.';source.textContent='CAMERA UNAVAILABLE';}
  }finally{
   liveBusy=false;
   if(token===epoch&&running&&!disposed)liveTimer=setTimeout(()=>{liveTimer=null;void loadLive(token);},document.hidden?1500:400);
  }
 }

 async function loadCapture(token){
  if(captureBusy||disposed||!runId)return;
  captureBusy=true;
  try{
   const response=await fetchImpl(`/api/v1/room-agent/camera/captures/${encodeURIComponent(runId)}/latest`,{cache:'no-store'});
   if(token!==epoch||disposed)return;
   if(response.status===204)return;
   if(!response.ok)throw Error('capture unavailable');
   const data=await response.json();
   if(token!==epoch||disposed||!data.observation_id)return;
   if(captureId===data.observation_id)return;
   const imageResponse=await fetchImpl(`/api/v1/room-agent/camera/captures/${encodeURIComponent(runId)}/${encodeURIComponent(data.observation_id)}`,{cache:'no-store'});
   if(!imageResponse.ok)throw Error('capture image unavailable');
   const nextUrl=URL.createObjectURL(await imageResponse.blob());
   if(token!==epoch||disposed){URL.revokeObjectURL(nextUrl);return;}
   if(captureUrl)URL.revokeObjectURL(captureUrl);
   captureUrl=nextUrl;captureId=data.observation_id;captureData=data;
   captureImage.src=nextUrl;captureImage.hidden=false;captureEmpty.hidden=true;
   if(data.width>0&&data.height>0)captureStage.style.aspectRatio=`${data.width} / ${data.height}`;
   const detector=mode==='LIVE'?'VLM':'simulated detector';
   captureImage.alt=data.verified
    ?`Drone capture from ${new Date(data.captured_at).toLocaleTimeString()} with ${data.detections.length} ${detector} detections`
    :`Saved drone capture from ${new Date(data.captured_at).toLocaleTimeString()} without verified analysis`;
   drawObjects(captureOverlay,data.detections,data.width,data.height);
   captureNote.textContent=data.verified
    ?`${data.detections.length} ${detector} ${data.detections.length===1?'object':'objects'} · ${data.provider} · ${new Date(data.captured_at).toLocaleTimeString()}. ${data.detections.map(d=>`${d.label} (${d.confidence})`).join(', ')}`
    :'Saved camera frame. No verified VLM result is available for this frame.';
   captureButton.textContent=data.verified?`Gemini capture · ${data.detections.length}`:'Saved PNG capture';
   if(!running&&view==='live'&&!liveAt)select('capture');
   updateSource();
  }catch{
   if(token===epoch)captureEmpty.textContent='Analyzed capture unavailable.';
  }finally{
   captureBusy=false;
   if(token===epoch&&running&&!disposed)captureTimer=setTimeout(()=>{captureTimer=null;void loadCapture(token);},1800);
  }
 }

 return {
  attachReview(panel){reviewPanel=panel;host.append(panel);panel.hidden=view!=='review';},
  render(run){
   const nextId=run?.id??null;
   if(nextId!==runId){clear();runId=nextId;mode=run?.mode??null;captureButton.textContent='Gemini capture';}
   reviewState=run?.review??null;
   running=run?.status==='RUNNING';
   if(!runId)liveEmpty.textContent='Camera starts after dispatch.';
   else if(!running&&!liveAt)liveEmpty.textContent='No camera frame was received for this run.';
   if(running&&!liveTimer&&!liveBusy)void loadLive(epoch);
   if(running&&!captureTimer&&!captureBusy)void loadCapture(epoch);
   if(!running&&runId&&!captureBusy&&terminalCaptureAttempts<6&&(!captureData||terminalCaptureAttempts===0)){
    terminalCaptureAttempts++;void loadCapture(epoch);
   }
   updateSource();
  },
  dispose(){disposed=true;clear();},
 };
}
