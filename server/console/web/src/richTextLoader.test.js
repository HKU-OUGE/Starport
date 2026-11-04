import test from 'node:test';
import assert from 'node:assert/strict';
import {createRichTextLoader,richModuleUrl} from './richTextLoader.js';
test('a slow renderer leaves basic content available and shares the in-flight read',async()=>{
 let done,slow,calls=0;const loader=createRichTextLoader(()=>{calls++;return new Promise(resolve=>done=resolve);},{schedule:fn=>{slow=fn;return 1;},cancel:()=>{}});
 const pending=loader.load();assert.equal(loader.load(),pending);await Promise.resolve();slow();
 assert.equal(loader.getSnapshot().phase,'slow');assert.equal(loader.getSnapshot().component,null);assert.equal(calls,1);
 const component=()=>null;done({default:component});await pending;assert.equal(loader.getSnapshot().component,component);
});
test('a failed optional renderer can be retried without refreshing a session or sending anything',async()=>{
 let count=0;const component=()=>null;
 const loader=createRichTextLoader(async()=>{if(++count===1)throw Error('offline');return {default:component};});
 await loader.load();assert.equal(loader.getSnapshot().phase,'error');assert.equal(loader.getSnapshot().component,null);
 await loader.load();assert.equal(loader.getSnapshot().phase,'ready');assert.equal(loader.getSnapshot().component,component);
});
test('renderer retries bypass a failed module cache while remaining on the same origin',()=>{
 const base='https://console.example.invalid/';
 assert.notEqual(richModuleUrl('/assets/rich-text-Abcdef12.js',base,1),richModuleUrl('/assets/rich-text-Abcdef12.js',base,2));
 assert.throws(()=>richModuleUrl('https://other.example.invalid/script.js',base,1));
 assert.throws(()=>richModuleUrl('/api/files/script.js',base,1));
});
