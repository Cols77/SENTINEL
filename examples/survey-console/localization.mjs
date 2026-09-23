import {el,button,meta,replace} from './dom.mjs';
export function poseStyle(status,containment){return containment==='OUTSIDE'?'pose-outside':{FRESH:'pose-fresh',AGING:'pose-aging',STALE:'pose-stale',UNAVAILABLE:'pose-unavailable'}[status];}
export function verticalConsistencyText(v){if(!v)return 'UNAVAILABLE - No vertical reference';if(v.error_m===null||v.error_m===undefined)return `${v.status} - ${v.reason||'No public reason'}`;return `${v.status} - ${v.error_m} m - ${v.reason||'No public reason'}`;}
export function tagPolygon(corners,width,height){
 if(corners.length!==4||corners.some(p=>p.length!==2||p.some(v=>!Number.isFinite(v)||v<0||v>1)))throw Error('Invalid tag geometry');
 return corners.map(([x,y])=>`${x*width},${y*height}`).join(' ');
}
export function createLocalizationView(host,client){
 const heading=el('h3',{},['Localization camera']);const health=meta('Pose unavailable');const stage=el('div',{class:'pose-image-stage'});
 const legend=meta('Measured pose · no current observation');const toggle=button('Hide localization tags',()=>{show=!show;stage.classList.toggle('hide-tags',!show);toggle.textContent=show?'Hide localization tags':'Show localization tags';});
 host.append(heading,health,toggle,stage,legend);
 let show=true,artifact=null,url=null,token=0,disposed=false;
 async function render(s){
  const p=s.localization;health.textContent=`${p.status} · ${p.age_ms===null?'no observation':`${Math.round(p.age_ms)} ms old`} · ${s.source.kind}`;
  health.className=`meta ${poseStyle(p.status,p.containment)}`;
  const h=p.recent_stream;legend.textContent=`${h.reports_emitted} fixes / ${h.frames_seen} frames · ${h.no_tag_frames} no-tag · ${h.gated_frames} gated · ${h.timeouts} timeouts · ${p.containment} · vertical ${verticalConsistencyText(p.vertical_consistency)}`;
  const evidence=p.pose_evidence;
  if(!evidence){token++;if(url)URL.revokeObjectURL(url);url=null;artifact=null;replace(stage,[meta('No authorized localization image')]);return;}
  if(artifact===evidence.artifact_id)return;
  artifact=evidence.artifact_id;const current=++token;
  try{
   const result=await client.read_pose_evidence(s.run.id,evidence);
   if(disposed||current!==token)return;
   if(url)URL.revokeObjectURL(url);url=URL.createObjectURL(new Blob([result.bytes],{type:result.media_type}));
   const img=el('img',{src:url,alt:`${s.session.mode} localization frame, report ${evidence.report_id}`});
   const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',`0 0 ${evidence.width_px} ${evidence.height_px}`);svg.setAttribute('aria-label','AprilTag detections');
   for(const tag of evidence.tags){const polygon=document.createElementNS(svg.namespaceURI,'polygon');polygon.setAttribute('points',tagPolygon(tag.corners_xy_normalized,evidence.width_px,evidence.height_px));polygon.setAttribute('class',tag.status==='USED'?'tag-used':'tag-unused');svg.append(polygon);const label=document.createElementNS(svg.namespaceURI,'text');label.setAttribute('x',String(tag.corners_xy_normalized[0][0]*evidence.width_px));label.setAttribute('y',String(tag.corners_xy_normalized[0][1]*evidence.height_px-6));label.textContent=`${tag.tag_id} ${tag.status}`;svg.append(label);}
   replace(stage,[img,svg]);
  }catch{if(current===token){artifact=null;replace(stage,[meta('Localization evidence unavailable')]);}}
 }
 return {render,dispose(){disposed=true;token++;if(url)URL.revokeObjectURL(url);}};
}
