import test from 'node:test';
import assert from 'node:assert/strict';
import {createConsoleApi,requestDeadline,validateSessionList,subscribeReadRecovery} from './consoleRequest.js';
import {deliveryStatus} from './activity.js';

const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
test('cached reads have short deadlines while mutations keep their original budget',()=>{
 for(const p of ['/api/sessions?background=1','/api/status?background=1','/api/session/example?delta=1'])assert.equal(requestDeadline(p,false),12000);
 assert.equal(requestDeadline('/api/me',false),10000);
 assert.equal(requestDeadline('/api/action',true),80000);
 assert.equal(requestDeadline('/api/artifacts/example',false),80000);
});
test('Access redirect becomes an explicit reauthentication state without following it',async()=>{
 let count=0;
 const api=createConsoleApi({fetcher:async(path,options)=>{count++;assert.equal(options.redirect,'manual');return {type:'opaqueredirect',status:0};}});
 await assert.rejects(api('/api/sessions'),e=>e.code==='access_login_required');assert.equal(count,1);
});
test('non-JSON access pages and invalid lists cannot silently remain loading',async()=>{
 const api=createConsoleApi({fetcher:async()=>new Response('<html>Access</html>',{status:403,headers:{'Content-Type':'text/html'}})});
 await assert.rejects(api('/api/sessions'),e=>e.code==='access_check_required');
 for(const value of [null,{}, {ok:true}, {sessions:'not a list'}])assert.throws(()=>validateSessionList(value),e=>e.code==='invalid_session_list');
 assert.deepEqual(validateSessionList({sessions:[]}),{sessions:[]});assert.ok(validateSessionList({loading:true}));
});
test('valid responses preserve session data and ordinary application authentication',async()=>{
 const payload={ok:true,sessions:[{id:'example'}]};
 assert.deepEqual(await createConsoleApi({fetcher:async()=>json(payload)})('/api/sessions'),payload);
 let expired=0;
 const api=createConsoleApi({onExpired:()=>expired++,fetcher:async()=>json({error:'authentication_required'},401)});
 await assert.rejects(api('/api/sessions'));assert.equal(expired,1);
 const unknown=createConsoleApi({fetcher:async()=>json({error:'temporary_server_issue'},503)});
 await assert.rejects(unknown('/api/sessions'),e=>e.code==='temporary_server_issue'&&e.message==='temporary_server_issue');
});
test('a timed-out send is uncertain and is never automatically retried',async()=>{
 let count=0;
 const api=createConsoleApi({schedule:fn=>{queueMicrotask(fn);return 1;},cancel:()=>{},fetcher:(path,options)=>{
  count++;return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}));
 }});
 await assert.rejects(api('/api/action',{operation_id:'keep-this-id'}),e=>e.code==='connection_timeout_outcome_unknown'&&deliveryStatus(e.code)==='unknown');
 assert.equal(count,1);
});
test('caller cancellation stays cancellation and never triggers a retry',async()=>{
 const controller=new AbortController();let count=0;
 const api=createConsoleApi({fetcher:async()=>{count++;controller.abort();throw new DOMException('aborted','AbortError');}});
 await assert.rejects(api('/api/session/example',undefined,{signal:controller.signal}),e=>e.name==='AbortError');assert.equal(count,1);
});
test('foreground recovery has one removable subscription and never calls a mutation',()=>{
 const doc=new EventTarget(),win=new EventTarget();doc.visibilityState='hidden';let reads=0;
 const dispose=subscribeReadRecovery(()=>reads++,doc,win);
 doc.dispatchEvent(new Event('visibilitychange'));assert.equal(reads,0);
 doc.visibilityState='visible';doc.dispatchEvent(new Event('visibilitychange'));win.dispatchEvent(new Event('pageshow'));assert.equal(reads,1);
 dispose();win.dispatchEvent(new Event('online'));assert.equal(reads,1);
});
