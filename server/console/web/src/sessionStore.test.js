import test from 'node:test';
import assert from 'node:assert/strict';
import {applyDelta, SessionStore} from './sessionStore.js';
const first = {delta: true, session_id:'a', reset:true, cursor:'v1', changes:{session:{id:'a'},partial:'hi'}, order:['x','y'],events:[{_event_id:'x',seq:1,text:'hello'},{_event_id:'y',seq:1,text:'world'}]};
test('same-sequence events, update/removal and stable references',()=>{
 const base=applyDelta(null,first,'a');
 const same=applyDelta(base,{...first,reset:false,base_cursor:'v1',unchanged:true,events:[]},'a');
 assert.equal(same,base);
 const next=applyDelta(base,{delta:true,session_id:'a',base_cursor:'v1',cursor:'v2',changes:{},removed_fields:['partial'],removed:['x'],order:['y','z'],events:[{_event_id:'z',seq:1,text:'new'}]},'a');
 assert.equal(next.events[0],base.events[1]);assert.equal(next.partial,undefined);assert.equal(next.events.length,2);
 assert.equal(base.events.length,2);
});
test('rejects stale/out-of-order or cross-session responses before merging',()=>{
 const base=applyDelta(null,first,'a');
 assert.throws(()=>applyDelta(base,{...first,reset:false,base_cursor:'v0'},'a'),/cursor/);
 assert.throws(()=>applyDelta(base,first,'b'),/session/);
 assert.throws(()=>applyDelta(null,{...first,order:['missing']},'a'),/resync/);
});
test('bounded LRU preserves selected messages and clears on logout',()=>{
 const store=new SessionStore(2,10000);store.set('a',{id:1});store.set('b',{id:2});store.get('a');store.set('c',{id:3});
 assert.equal(store.get('b'),null);assert.deepEqual(store.get('a'),{id:1});
 store.set('huge',{text:'x'.repeat(10001)});assert.equal(store.get('huge'),null);assert.ok(store.bytes<=10000);
 store.clear();assert.equal(store.get('a'),null);assert.equal(store.bytes,0);
});
