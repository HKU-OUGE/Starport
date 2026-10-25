import test from 'node:test';
import assert from 'node:assert/strict';
import {localPdf,documentReferences,attachDocuments,documentReadKey,registeredReference} from './documentFiles.js';

test('finds sent PDFs and local report links without proxying external or arbitrary references',()=>{
  const tool={role:'tool',tool_name:'SendUserFile',input:{files:['reports/test.pdf','https://example.com/test.pdf','private.key']}};
  assert.deepEqual(documentReferences(tool),['reports/test.pdf']);
  assert.deepEqual(documentReferences({role:'assistant',text:'[Report](<reports/two words.pdf>)'}),['reports/two words.pdf']);
  assert.equal(localPdf('file:///private/secret.pdf'),false);
  assert.equal(localPdf('bad%encoding.pdf'),false);
  assert.equal(documentReadKey([tool,tool]),'["reports/test.pdf"]');
});

test('renders loading and unavailable states and resolves exact registered references',()=>{
  const event={role:'assistant',text:'[Read](reports/test.pdf)'};
  const pending=attachDocuments([event],[],true)[0];
  assert.equal(pending.attachments[0].available,false);assert.equal(pending.attachments[0].pending,true);
  const file={reference:'reports/test.pdf',id:'opaque',mime:'application/pdf',available:true,source:'document'};
  assert.deepEqual(attachDocuments([event],[file])[0].attachments,[file]);
  assert.deepEqual(attachDocuments([{...event,attachments:[{...file,available:false}]}],[file])[0].attachments,[file]);
  assert.equal(attachDocuments([event],[{...file,reference:'other.pdf'}])[0].attachments[0].available,false);
  assert.deepEqual(event,{role:'assistant',text:'[Read](reports/test.pdf)'});
});

test('encoded Chinese Markdown links resolve only to a registered file',()=>{
  const files=new Map([['reports/報告.pdf','/api/documents/session/opaque']]);
  assert.equal(registeredReference(files,encodeURI('reports/報告.pdf')),'/api/documents/session/opaque');
  assert.equal(registeredReference(files,'/etc/secret.pdf'),undefined);
  assert.equal(registeredReference(files,'bad%encoding'),undefined);
});
