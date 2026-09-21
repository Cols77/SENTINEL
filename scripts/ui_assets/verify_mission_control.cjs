const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const path=require('node:path');
let base=process.argv[2],server,dataDir;
async function startHost(){
 if(base)return;
 dataDir=fs.mkdtempSync(path.resolve('artifacts/mission-control-test-'));
 const net=require('node:net');const probe=net.createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));
 base=`http://127.0.0.1:${port}`;
 server=spawn(path.resolve('.venv/bin/python'),['-m','sentinel.ui','--data-dir',dataDir,'--port',String(port)],{stdio:'ignore'});
 for(let n=0;n<100;n++){try{if((await fetch(base+'/api/v1/session')).ok)return;}catch{}await new Promise(r=>setTimeout(r,100));}
 throw Error('Test host did not start');
}
async function stopHost(){if(server){server.kill('SIGTERM');await new Promise(r=>{server.once('exit',r);setTimeout(r,5000);});server=null;}if(dataDir){fs.rmSync(dataDir,{recursive:true,force:true});dataDir=null;}}
(async()=>{
 await startHost();
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 page.setDefaultTimeout(5000);
 const errors=[],external=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(!r.url().startsWith(base+'/')&&!r.url().startsWith('blob:'))external.push(r.url());});
 try{
  await page.goto(base+'/console/');
  const independentSafety=await page.evaluate(async()=>{
   const {createSafetyView}=await import('/console/safety.mjs');
   const host=document.createElement('div');document.body.append(host);
   const snapshot={run:{id:'r'},session:{id:'s',mode:'REHEARSAL'},safety:{land:{available:true},emergency:{available:true}}};
   let release;const blocked=new Promise(resolve=>release=resolve);
   const view=createSafetyView(host,{request_land:()=>blocked,trigger_emergency:async()=>({status:'ACKNOWLEDGED'})},()=>snapshot);
   view.render(snapshot);host.querySelector('#land-now').click();view.render(snapshot);
   const available=!host.querySelector('#emergency-stop').disabled;
   release({status:'ACKNOWLEDGED'});view.dispose();host.remove();return available;
  });
  assert.equal(independentSafety,true,'Emergency must remain available during a pending Land request');
  await page.getByRole('textbox',{name:'Mission request'}).fill('count the computers');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await page.getByRole('button',{name:'Approve flight',exact:true}).waitFor();
  await page.reload();
  await page.locator('#localization .pose-stale').waitFor({timeout:10000});
  await page.locator('#localization .pose-fresh').waitFor({timeout:10000});
  assert.equal(await page.getByRole('button',{name:'Approve flight',exact:true}).isEnabled(),true);
  await page.locator('#localization svg polygon').first().waitFor();
  await page.route('**/decisions',async route=>{await route.fetch();await route.abort();},{times:1});
  await page.getByRole('button',{name:'Approve flight',exact:true}).click();
  await page.locator('#accepted-report').waitFor();
  await page.reload();
  await page.locator('#accepted-report').waitFor();
  assert.match(await page.locator('#accepted-report').innerText(),/1 computers/);
  await page.locator('[data-frame-id]').first().click();
  await page.locator('#evidence img').waitFor();
  const session=await (await page.request.get(base+'/api/v1/session')).json();
  const snapshot=await (await page.request.get(`${base}/api/v1/runs/${session.active_run_id}/snapshot`)).json();
  assert.equal(snapshot.results.counts.find(c=>c.class_name==='computer').confirmed,1);
  assert.equal(snapshot.report.status,'ACCEPTED');
  assert.equal(snapshot.mission.flights.used,1);
  const evidenceLabels=await page.evaluate(async()=>{
   const {createEvidenceView}=await import('/console/evidence.mjs');
   const {createHttpClient}=await import('/console/http-client.mjs');
   const {toSurveyView}=await import('/console/view-model.mjs');
   const client=createHttpClient();const session=await client.session();const s=await client.read_survey(session.active_run_id);
   const viewSnapshot=toSurveyView(s);viewSnapshot.mode='LIVE';viewSnapshot.source='LIVE';viewSnapshot.frames.forEach(f=>f.source_kind='LIVE');
   const host=document.createElement('div');document.body.append(host);const view=createEvidenceView(host,client);
   await view.select(viewSnapshot,{kind:'frame',id:viewSnapshot.frames[0].id});
   const result={alt:host.querySelector('img')?.alt,text:host.textContent};view.dispose();host.remove();return result;
  });
  assert.match(evidenceLabels.alt,/LIVE capture/);assert.doesNotMatch(evidenceLabels.text,/Synthetic image only/);
  const poseRevocation=await page.evaluate(async()=>{
   const {createLocalizationView}=await import('/console/localization.mjs');
   const {createHttpClient}=await import('/console/http-client.mjs');
   const client=createHttpClient();const session=await client.session();const s=await client.read_survey(session.active_run_id);
   const host=document.createElement('div');document.body.append(host);
   const canvas=document.createElement('canvas');canvas.width=1;canvas.height=1;
   const bytes=Uint8Array.from(atob(canvas.toDataURL().split(',')[1]),c=>c.charCodeAt(0));
   s.localization.pose_evidence={artifact_id:'a',report_id:'r',width_px:1,height_px:1,tags:[]};
   let release;const view=createLocalizationView(host,{read_pose_evidence:()=>new Promise(r=>release=r)});
   const pending=view.render(s);const revoked=structuredClone(s);revoked.localization.pose_evidence=null;
   await view.render(revoked);release({bytes,media_type:'image/png'});await pending;
   const lateRemoved=!host.querySelector('img');
   const display=view.render(s);release({bytes,media_type:'image/png'});await display;
   const initiallyVisible=!!host.querySelector('img');await view.render(revoked);
   const removed=!host.querySelector('img');view.dispose();host.remove();return {lateRemoved,initiallyVisible,removed};
  });
  assert.deepEqual(poseRevocation,{lateRemoved:true,initiallyVisible:true,removed:true});

  const emergency=page.locator('#emergency-stop');
  await emergency.focus();await page.keyboard.down('Space');await page.waitForTimeout(1250);await page.keyboard.up('Space');
  await page.getByText('Emergency: ACKNOWLEDGED · simulated transport',{exact:true}).waitFor();
  for(const viewport of [{width:1440,height:900},{width:1920,height:1080},{width:390,height:844}]){
   await page.setViewportSize(viewport);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  }
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.mkdirSync('artifacts/mission-control',{recursive:true});
  await page.screenshot({path:'artifacts/mission-control/acceptance.png',fullPage:true});
  console.log('PASS real-host request, reload, approval, runtime count, report, evidence, three viewports, local-only traffic');
 }finally{await browser.close();await stopHost();}
})().catch(async e=>{console.error(e);await stopHost();process.exitCode=1;});
