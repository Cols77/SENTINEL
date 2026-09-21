const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.argv[2]||'http://127.0.0.1:8770';
const output=path.resolve(__dirname,'../../artifacts/survey-console');
async function main(){
 fs.mkdirSync(output,{recursive:true});const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(5000);
 const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base+'/')&&!r.url().startsWith('blob:'))external.push(r.url());});
 try{
  await page.goto(base+'/examples/survey-console/');await page.locator('#scenario').waitFor();
  assert.equal(await page.locator('[data-testid]').count(),5);assert.match(await page.locator('[data-testid="Header"]').innerText(),/FIXTURE DEMO/);
  assert.equal(await page.locator('#emergency-disconnected').isDisabled(),true);
  assert.equal(await page.locator('#legacy-view').getAttribute('href'),'../../assets/sentinel-ui/console.html');
  assert.equal(await page.locator('#request input,#request textarea').count(),0);
  const before=await page.locator('#run-stepper').innerText();await page.waitForTimeout(100);assert.equal(await page.locator('#run-stepper').innerText(),before);
  await page.locator('#next-snapshot').click();assert.equal(await page.locator('[aria-current="step"]').innerText(),'PROPOSAL');assert.equal(await page.locator('[data-stop-id]').count(),3);
  await page.locator('#next-snapshot').focus();await page.keyboard.press('Enter');assert.equal(await page.locator('[aria-current="step"]').innerText(),'APPROVAL');
  await page.keyboard.press('Space');assert.equal(await page.locator('[aria-current="step"]').innerText(),'FLIGHT');
  await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('[data-stop-id]').first().evaluate(e=>getComputedStyle(e).animationName),'none');
  const boxes=await page.locator('[data-testid]').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};}));assert.ok(boxes.every(r=>r.x>=0&&r.y>=0&&r.w>0&&r.h>0&&r.x+r.w<=1441&&r.y+r.h<=901));
  await page.screenshot({path:path.join(output,'console-1440.png')});
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.evaluate(async()=>{const {renderShell}=await import('/examples/survey-console/shell.mjs');const {makeScenario}=await import('/examples/survey-console/fixtures.mjs');const s=makeScenario('computers-followup').snapshots[0];s.request_text='<img src=x onerror=alert(1)>';renderShell(document.getElementById('app'),{survey:s,events:[],needs_refresh:false,log_gaps:[],error:null},{});});
  assert.match(await page.locator('#request').innerText(),/<img src=x onerror=alert\(1\)>/);assert.equal(await page.locator('#request img').count(),0);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(output,'validation.json'),JSON.stringify({status:'passed',stage:'shell',errors,external},null,2));console.log('Survey console browser checks passed (shell).');
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
