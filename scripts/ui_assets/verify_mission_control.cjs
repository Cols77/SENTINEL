const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.argv[2]||'http://127.0.0.1:8771';
(async()=>{
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
  await page.getByRole('button',{name:'Approve flight',exact:true}).click();
  await page.locator('#accepted-report').waitFor();
  assert.match(await page.locator('#accepted-report').innerText(),/1 computers/);
  await page.locator('[data-frame-id]').first().click();
  await page.locator('#evidence img').waitFor();
  const session=await (await page.request.get(base+'/api/v1/session')).json();
  const snapshot=await (await page.request.get(`${base}/api/v1/runs/${session.active_run_id}/snapshot`)).json();
  assert.equal(snapshot.results.counts.find(c=>c.class_name==='computer').confirmed,1);
  assert.equal(snapshot.report.status,'ACCEPTED');
  for(const viewport of [{width:1440,height:900},{width:1920,height:1080},{width:390,height:844}]){
   await page.setViewportSize(viewport);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  }
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.mkdirSync('artifacts/mission-control',{recursive:true});
  await page.screenshot({path:'artifacts/mission-control/acceptance.png',fullPage:true});
  console.log('PASS real-host request, reload, approval, runtime count, report, evidence, three viewports, local-only traffic');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
