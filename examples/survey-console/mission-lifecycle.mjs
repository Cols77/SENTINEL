export function startMissionLifecycle({startPolling,registerCleanup,dispose,loadProfiles,onLoadError=()=>{}}){
 const stop=startPolling();
 let disposed=false;
 const cleanup=()=>{
  if(disposed)return;
  disposed=true;
  stop();
  dispose();
 };
 registerCleanup(cleanup);
 void Promise.resolve().then(()=>{if(disposed)return;return loadProfiles();}).catch(onLoadError);
 return cleanup;
}
