import test from 'node:test';
import assert from 'node:assert/strict';
import {sendRequest} from './sendRequest.js';
test('uncertain identical sends retain operation identity',()=>{
 const previous={session_id:'one',message:'简体消息',operation_id:'prior',status:'unknown'};
 assert.equal(sendRequest(previous,'one','简体消息',()=>{throw Error('must reuse')}).operation_id,'prior');
 assert.equal(sendRequest({...previous,status:'submitting'},'one','简体消息',()=>{throw Error('must reuse')}).operation_id,'prior');
});
test('confirmed rejection, changed text or changed session gets a new identity',()=>{
 const previous={session_id:'one',message:'简体消息',operation_id:'prior',status:'unknown'};
 for(const [old,id,message] of [[{...previous,status:'rejected'},'one','简体消息'],[previous,'two','简体消息'],[previous,'one','新的消息']])assert.equal(sendRequest(old,id,message,()=>'new').operation_id,'new');
});
