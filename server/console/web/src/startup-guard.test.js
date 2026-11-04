import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('./startup-guard.js',import.meta.url),'utf8');
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function fixture(responses=[],storage=new Map()){
 const nodes=Object.fromEntries(['console-bootstrap','console-startup-status','console-startup-code','console-startup-retry'].map(id=>[id,{textContent:'',href:''}]));
 const handlers=new Map(),timers=new Map(),requests=[],reloads=[];let next=0;
 const window={location:{href:'https://console.example.invalid/',replace:url=>reloads.push(url)},addEventListener:(name,fn)=>handlers.set(name,fn)};
 const context={window,document:{readyState:'loading',getElementById:id=>nodes[id],querySelector:()=>({src:'https://console.example.invalid/assets/index-fixture.js'})},
  URL,Date,Promise,Error,AbortController,
  sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
  setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next;},clearTimeout:id=>timers.delete(id),
  fetch:async(url,options)=>{requests.push({url,options});return responses.shift()||{ok:true,status:200,headers:{get:()=> 'text/javascript'}};}
 };
 vm.runInNewContext(source,context);
 return {nodes,requests,reloads,timers,emit:(name,event={})=>handlers.get(name)?.(event),run:ms=>{for(const [id,item] of timers)if(item.ms===ms){timers.delete(id);item.fn();return true;}return false;}};
}
test('watchdog starts before DOMContentLoaded and readiness cancels it',()=>{
 const f=fixture();assert.ok([...f.timers.values()].some(x=>x.ms===12000));
 f.emit('console-startup-ready');assert.equal(f.run(12000),false);assert.equal(f.requests.length,0);
});
test('slow startup probes only same-origin reads and does not reload a slow download',async()=>{
 const f=fixture();f.run(12000);await flush();
 assert.match(f.nodes['console-startup-code'].textContent,/startup_pending/);
 assert.equal(f.run(1000),false);
 assert.ok(f.requests.every(x=>x.options.method==='HEAD'&&x.options.redirect==='manual'));
});
test('Access redirects are named and startup auto reload is bounded across page loads',async()=>{
 const storage=new Map(),redirect={type:'opaqueredirect',status:0};
 const f=fixture([redirect],storage);f.emit('console-startup-failed',{detail:{code:'startup_module_failed'}});await flush();
 assert.match(f.nodes['console-startup-code'].textContent,/startup_access_required/);f.run(1000);assert.equal(f.reloads.length,1);
 const second=fixture([redirect],storage);second.emit('console-startup-failed',{detail:{code:'startup_module_failed'}});await flush();
 assert.equal(second.run(1000),false);assert.equal(second.reloads.length,0);
});
test('runtime incompatibility shows a safe code without exposing exception contents',()=>{
 const f=fixture();f.emit('error',{error:{name:'SyntaxError',message:'private exception content'}});
 assert.match(f.nodes['console-startup-code'].textContent,/startup_syntax_error/);
 assert.ok(!f.nodes['console-startup-status'].textContent.includes('private exception'));
 assert.equal(f.requests.length,0);assert.equal(f.run(1000),false);
});
test('successful application startup prevents later failures from reloading a conversation',async()=>{
 const f=fixture();f.emit('console-startup-ready');delete f.nodes['console-bootstrap'];
 f.emit('console-startup-failed',{detail:{code:'startup_module_failed'}});f.emit('unhandledrejection');await flush();
 assert.equal(f.requests.length,0);assert.equal(f.reloads.length,0);
});
