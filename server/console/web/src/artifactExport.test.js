import test from 'node:test';
import assert from 'node:assert/strict';
import {artifactHtml} from './artifactExport.js';
import {replayArtifact} from './artifactReplay.js';

test('standalone export contains MathML and verified inline media, never scripts',()=>{
 const value={title:'<script>bad</script>',available:true,content:{tag:'article',children:[{tag:'math',text:'x^2'},{tag:'figure',caption:'Example',image:{src:'data:image/jpeg;base64,/9j/AA=='}},{tag:'a',href:'javascript:alert(1)',children:[{tag:'text',text:'<unsafe>'}]},{tag:'script',children:[{tag:'text',text:'alert(1)'}]}]}};
 const html=artifactHtml(value);assert.match(html,/<math/);assert.match(html,/data:image\/jpeg/);assert.doesNotMatch(html,/<script|javascript:alert/);assert.match(html,/&lt;unsafe&gt;/);assert.match(html,/Content-Security-Policy/);
});
test('accepted edits restore paragraphs, tables and exact find replacements',()=>{
 const records=[{node:'n',revision:1,captured_at:'1',op:{op:'insert',target:{kind:'root'},side:'end',source:{as:'markdown',from:{kind:'inline',content:'# Title\n\nOld text'}}},blocks:[{id:'a'},{id:'b'}]},
 {node:'n',revision:2,captured_at:'2',op:{op:'replace',target:{kind:'find',text:'Old text'},with:{as:'text',from:{kind:'inline',content:'New text'}}},blocks:[{id:'b'}]}];
 const view=replayArtifact({replay:records},{});assert.equal(view.revision,2);assert.equal(view.operations_applied,2);assert.equal(view.toc.length,1);assert.match(JSON.stringify(view.content),/New text/);assert.doesNotMatch(JSON.stringify(view.content),/Old text/);
 records[1].op.target.text='does not exist';assert.throws(()=>replayArtifact({replay:records},{}),/ambiguous_find/);
});
test('unacknowledged alignment and missing sections fail closed',()=>{
 const r={node:'n',op:{op:'insert',target:{kind:'root'},side:'end',source:{as:'markdown',from:{kind:'inline',content:'One\n\nTwo'}}},blocks:[{id:'a'}]};
 assert.throws(()=>replayArtifact({replay:[r]},{}),/alignment/);
 r.op.source={as:'markdown',from:{kind:'inline',content:'<?claude block wait?>'},blocks:{wait:{type:'pending',intent:'unfinished'}}};
 assert.throws(()=>replayArtifact({replay:[r]},{}),/unresolved_section/);
});
test('table edits preserve data-row coordinates and require matching acknowledgements',()=>{
 const start={node:'n',revision:1,op:{op:'insert',target:{kind:'root'},side:'end',source:{as:'markdown',from:{kind:'inline',content:'| Metric | Current | Prior |\n|---|---|---|\n| Rate | 62% | 66% |'}}},blocks:[{id:'table'}]};
 const edit={node:'n',revision:2,op:{op:'replace',target:{kind:'cell',table:'table',row:1,col:2},with:{as:'markdown',from:{kind:'inline',content:'38%'}}},blocks:[{id:'table',text:'38%'}]};
 const v=replayArtifact({replay:[start,edit]},{});const cells=v.content.children[0].children[1].children;
 assert.equal(cells[1].children[0].text,'38%');assert.equal(cells[2].children[0].text,'66%');
 edit.blocks[0].text='different';assert.throws(()=>replayArtifact({replay:[start,edit]},{}),/ack_mismatch/);
});
