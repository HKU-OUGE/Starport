import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

test('older browser helpers preserve negative indexes, holes and own properties',()=>{
 const context=vm.createContext({});
 vm.runInContext('delete Array.prototype.at;delete Array.prototype.findLastIndex;delete Object.hasOwn;',context);
 vm.runInContext(readFileSync(new URL('./browserCompat.js',import.meta.url),'utf8'),context);
 const check=code=>vm.runInContext(code,context);
 assert.equal(check('[1,2,3].at(-1)'),3);
 assert.equal(check('[1,2].at(-3)'),undefined);
 assert.equal(check('[1,2,1].findLastIndex(x=>x===1)'),2);
 assert.equal(check('[1,,3].findLastIndex(x=>x===undefined)'),1);
 assert.equal(check('Object.hasOwn(Object.create({inherited:true}),"inherited")'),false);
 assert.equal(check('Object.hasOwn({own:false},"own")'),true);
 assert.equal(check('Object.keys(Array.prototype).includes("at")'),false);
});
