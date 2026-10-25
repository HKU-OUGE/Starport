import test from 'node:test';
import assert from 'node:assert/strict';
import {presentation,duration,fallbackActivity} from './activity.js';
const session={id:'cse_example',kind:'remote',status:'running'};
test('live reported activity increments from receipt, not page mount',()=>{
 const state={session:session.id,value:{phase:'thinking',fresh:true,timer_running:true,elapsed_ms:65000,timer_basis:'reported'},receivedAt:100000};
 const a=presentation(session,null,state,102000);assert.equal(a.elapsed,67000);assert.equal(a.label,'Thinking · 思考中');assert.equal(a.timerLabel,'本轮');
});
test('a stale or disconnected read freezes and no longer claims thinking',()=>{
 const state={session:session.id,value:{phase:'thinking',fresh:true,timer_running:true,elapsed_ms:65000},receivedAt:100000};
 const a=presentation(session,null,state,130000);assert.equal(a.phase,'synchronizing');assert.equal(a.elapsed,65000);
 assert.equal(presentation(session,null,{...state,error:'offline'},101000).phase,'synchronizing');
});
test('complete and permission states are preserved',()=>{
 for(const phase of ['complete','waiting_approval','waiting_input']){
  const state={session:session.id,value:{phase,fresh:true,timer_running:false,elapsed_ms:12000},receivedAt:100000};
  const a=presentation(session,null,state,105000);assert.equal(a.phase,phase);assert.equal(a.elapsed,12000);
 }
});
test('changing sessions never displays prior session thinking',()=>{
 const state={session:'other',value:{phase:'thinking',fresh:true},receivedAt:100000};
 assert.notEqual(presentation(session,{events:[]},state,100000).phase,'thinking');
});
test('history and missing fields never invent a thinking state',()=>{
 assert.equal(fallbackActivity({id:'h',status:'history'},{events:[]},100000).phase,'history');
 assert.equal(fallbackActivity(session,{events:[]},100000).phase,'working');
 assert.equal(fallbackActivity(session,{events:[]},100000).elapsed_ms,null);
});
test('tool results close pending tools and fallback timer is stable',()=>{
 const events=[{role:'user',time:'2026-10-25T00:00:00Z'},{role:'tool',tool_id:'t',tool_name:'Bash'}];
 const now=Date.parse('2026-10-25T00:01:00Z');
 assert.equal(fallbackActivity(session,{events},now).phase,'tool');
 assert.equal(fallbackActivity(session,{events:[...events,{role:'tool_result',tool_id:'t'}]},now).phase,'working');
 assert.equal(fallbackActivity(session,{events},now).elapsed_ms,60000);
});
test('duration formats zero, long turns, and unknown correctly',()=>{
 assert.equal(duration(0),'0:00');assert.equal(duration(90000),'1:30');assert.equal(duration(3601000),'1:00:01');assert.equal(duration(null),'—');
});

import {deliveryStatus} from './activity.js';
test('only known rejections allow retry; uncertain sends stay uncertain',()=>{assert.equal(deliveryStatus('remote_login_required'),'rejected');assert.equal(deliveryStatus('remote_send_outcome_unknown'),'unknown');assert.equal(deliveryStatus(undefined),'unknown');});
