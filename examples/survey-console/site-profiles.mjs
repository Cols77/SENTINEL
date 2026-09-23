const feedback={
 MISSION_NOT_IDLE:'The mission has begun. Site selection is locked.',
 PROFILE_SWITCH_UNAVAILABLE:'Site selection is unavailable. Check the local runtime.',
 SITE_PROFILE_NOT_FOUND:'That site profile is no longer available.',
 SITE_PROFILE_DISABLED:'This site profile is still a draft.',
 SITE_PROFILE_INCOMPLETE:'This site profile needs calibration before use.',
 INVALID_LIVE_SITE_OR_COVERAGE:'This site profile has invalid site or coverage calibration.',
 INVALID_PROFILE_REQUEST:'Choose a valid site profile.',
};

export function profileActivityKey(snapshot){
 return JSON.stringify([snapshot.run.id,snapshot.run.status,snapshot.run.phase,
  snapshot.run.decision_version??null,snapshot.events.watermark,
  snapshot.chat?.messages?.at(-1)?.id??null,snapshot.proposal?.id??null]);
}

export function createProfileActivityWatcher(load){
 let previous=null;
 return snapshot=>{
  if(snapshot?.session.mode!=='LIVE')return;
  const key=profileActivityKey(snapshot);
  if(key===previous)return;
  previous=key;
  return load();
 };
}

export function createSiteProfileController(client,refreshSnapshot=async()=>{}){
 let state={profiles:[],selected_profile_id:null,switch_allowed:false,switch_reason:null,pending:false,feedback:''};
 let revision=0,loading=null,rerun=false;
 const listeners=new Set();
 const emit=()=>{for(const listener of listeners)listener({...state,profiles:state.profiles.map(profile=>({...profile}))});};
 const update=catalogue=>{
  revision++;
  state={...state,profiles:catalogue.profiles??[],selected_profile_id:catalogue.selected_profile_id??null,
   switch_allowed:catalogue.switch_allowed===true,switch_reason:catalogue.switch_reason??null,
   feedback:catalogue.switch_reason?feedback[catalogue.switch_reason]??'Site selection is unavailable.':''};
  emit();
 };
 const api={
  getState:()=>({...state,profiles:state.profiles.map(profile=>({...profile}))}),
  subscribe(listener){listeners.add(listener);return ()=>listeners.delete(listener);},
  update,
  async load(){
   if(loading){rerun=true;return loading;}
   loading=(async()=>{
    do{
     rerun=false;
     const started=revision;
     try{
      const catalogue=await client.read_site_profiles();
      if(started===revision&&!state.pending)update(catalogue);
      else rerun=true;
     }catch{
      if(started===revision&&!state.pending){state={...state,feedback:'Site catalogue unavailable.'};emit();}
      else rerun=true;
     }
    }while(rerun&&!state.pending);
   })().finally(()=>{loading=null;});
   return loading;
  },
  async select(profile_id){
   if(state.pending||!state.switch_allowed||profile_id===state.selected_profile_id||!state.profiles.some(profile=>profile.id===profile_id&&profile.enabled))return;
   const previous=state.selected_profile_id;
   revision++;state={...state,pending:true,feedback:''};emit();
   try{
    const result=await client.select_site_profile(profile_id);
    if(result.status==='ACKNOWLEDGED'){
     update(result);state={...state,pending:false};emit();
     try{await refreshSnapshot();}catch{state={...state,feedback:'Site changed; snapshot refresh unavailable.'};emit();}
    }else{
     update(result);state={...state,selected_profile_id:previous,pending:false,feedback:feedback[result.reason_code]??'Site selection was refused.'};emit();
    }
   }catch{
    state={...state,selected_profile_id:previous,pending:false,feedback:'Site selection could not be confirmed. Refresh the catalogue.'};emit();
   }
   if(loading)await loading;
   if(rerun){rerun=false;await api.load();}
 },
 };
 return api;
}
