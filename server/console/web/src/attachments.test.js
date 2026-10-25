import test from 'node:test';
import assert from 'node:assert/strict';
import {sendRequest} from './sendRequest.js';

test('uncertain sends keep both attachment identity and operation ID',()=>{
 const previous={session_id:'session',message:'',attachments:['file-a'],operation_id:'previous',status:'unknown'};
 assert.equal(sendRequest(previous,'session','',()=> 'new',['file-a']).operation_id,'previous');
 assert.equal(sendRequest(previous,'session','',()=> 'new',['file-b']).operation_id,'new');
 assert.deepEqual(sendRequest(null,'session','',()=> 'new',['file-a']).attachments,['file-a']);
});
