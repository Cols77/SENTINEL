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
  await page.getByRole('textbox',{name:'Mission request'}).fill('count the computers');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await page.getByRole('button',{name:'Approve flight',exact:true}).waitFor();
  await page.reload();
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
