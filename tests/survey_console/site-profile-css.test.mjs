import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

test('narrow site placard keeps the selector and status in its single explicit track',()=>{
 const css=readFileSync(new URL('../../examples/survey-console/console.css',import.meta.url),'utf8');
 const narrow=css.slice(css.indexOf('@media(max-width:680px)'));
 assert.match(narrow,/\.site-profile-placard\{grid-template-columns:minmax\(0,1fr\)\}/);
 for(const child of ['select','p','#retry-site-profiles']){
  assert.match(narrow,new RegExp(`\\.site-profile-placard ${child}\\{grid-column:1/-1`));
 }
});
