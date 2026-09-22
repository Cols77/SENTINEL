import {el,meta,replace} from './dom.mjs';
import {poseStyle} from './localization.mjs';

export function missionMapModel(s){return {
 envelope:s.mission.envelope,route:s.mission.route,activeStop:s.mission.active_stop_id,
 commanded:s.mission.commanded_position,measured:s.localization.map_position,
 measuredFreshness:s.localization.status,poseClass:poseStyle(s.localization.status,s.localization.containment),
 returnToStart:s.mission.return_to_start,returnError:s.mission.return_error_cm,
 verticalConsistency:s.localization.vertical_consistency,
};}

export function renderMissionMap(host,m){
 if(!m.envelope){replace(host,[el('h2',{},['Mission workspace']),meta('Route unavailable')]);return;}
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 600 250');svg.setAttribute('role','img');svg.setAttribute('aria-label','Survey route with commanded and measured positions');
 const make=(name,attrs,text)=>{const n=document.createElementNS(svg.namespaceURI,name);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text)n.textContent=text;svg.append(n);return n;};
 const e=m.envelope,xy=p=>({x:40+520*(p.x-e.min_x)/Math.max(1,e.max_x-e.min_x),y:205-160*(p.y-e.min_y)/Math.max(1,e.max_y-e.min_y)});
 make('rect',{x:40,y:45,width:520,height:160,fill:'none',stroke:'#53636d','stroke-dasharray':'4 4'});
 const points=m.route.map(xy);make('polyline',{points:points.map(p=>`${p.x},${p.y}`).join(' '),fill:'none',stroke:'#718e9a','stroke-width':2});
 m.route.forEach((stop,i)=>{const p=points[i];const coverage=stop.coverage||'MARGINAL';const status=stop.status||'PLANNED';const cls=`route-stop status-${status.toLowerCase()} coverage-${coverage.toLowerCase()}${stop.return_leg?' return-leg':''}`;const node=make('circle',{cx:p.x,cy:p.y,r:stop.stop_id===m.activeStop?8:5,fill:stop.return_leg?'#e8b45e':'#b5c6cc',class:cls,'data-stop-id':stop.stop_id,'data-status':status,'data-coverage':coverage,'data-return-leg':String(Boolean(stop.return_leg))});node.setAttribute('aria-label',`${stop.stop_id} ${status} ${coverage}${stop.return_leg?' return leg':''}`);make('text',{x:p.x,y:p.y+24,'text-anchor':'middle',fill:'#cad5d9','font-size':14},String(i+1));});
 if(m.commanded){const p=xy(m.commanded);make('path',{d:`M ${p.x} ${p.y-10} l 10 10 l -10 10 l -10 -10 Z`,fill:'none',stroke:'#c7d1d7','stroke-width':2,'data-marker':'commanded'});}
 if(m.measured){const p=xy(m.measured);make('circle',{cx:Math.max(10,Math.min(590,p.x)),cy:Math.max(10,Math.min(240,p.y)),r:9,class:m.poseClass,fill:m.poseClass==='pose-stale'?'none':'currentColor',stroke:'currentColor','stroke-width':2,'stroke-dasharray':m.poseClass==='pose-stale'?'3 3':'none','data-marker':'measured','data-freshness':m.measuredFreshness});}
 const facts=m.route.map(stop=>`${stop.stop_id}: ${stop.status||'PLANNED'} - ${stop.coverage||'MARGINAL'} - ${stop.coverage_reason||'coverage unavailable'} - ${stop.predicted_tag_count??'--'} tags - fix ${stop.predicted_fix_error_cm??'--'} cm - ${stop.trim_progress??0}/${stop.trim_budget??0} trims - ${stop.leg_speed_cm_s??'--'} cm/s - arrival ${stop.arrival_error_cm??'--'} cm / tol ${stop.tolerance_cm??'--'} cm ${stop.tolerance_verdict||'UNKNOWN'}${stop.return_leg?' - RETURN':''}`);
 const returnFacts=m.returnToStart?[meta(`Return to start ${m.returnError===null||m.returnError===undefined?'pending':`${m.returnError} cm error`}`)]:[];
 replace(host,[el('h2',{},['Mission workspace']),svg,meta('Diamond: commanded - Circle: measured - Dashed: stale - Amber: return leg'),el('div',{class:'route-facts','aria-label':'Route operational facts'},facts.map(text=>meta(text))),...returnFacts]);
}
