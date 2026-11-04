const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {guide,normalize,agentPrompt,skills}=require('../docs/quickstart/guide.js');
test('all nine OS pairs select the right shell and client transport',()=>{
 for(const a of ['linux','mac','windows'])for(const b of ['linux','mac','windows']){
  const steps=guide({a,b,startupA:true,startupB:true});assert.equal(steps.length,8);
  const client=steps[2].blocks[0].code,server=steps[4].blocks[0].code;
  assert.ok(client.startsWith(b==='windows'?'py -3':'python3'));
  assert.ok(server.startsWith(a==='windows'?'py -3':'python3'));
  assert.equal(server.includes('--client-platform windows'),b==='windows');
  assert.ok(!server.includes('--no-register'));assert.ok(!client.includes('--yes'));
 }
});
test('manual mode uses real no-register option and a foreground runner',()=>{
 const steps=guide({a:'windows',b:'mac',startupA:false,startupB:false});
 assert.match(steps[2].blocks[0].code,/--no-register/);
 assert.equal(steps[2].blocks[1].code,'python3 tools/run_services.py client');
 assert.equal(steps[2].blocks[2].code,'python3 starship.py status');
 assert.match(steps[4].blocks[0].code,/--no-register/);
 assert.equal(steps[4].blocks[1].code,'py -3 tools/run_services.py server');
 assert.match(steps[4].blocks[2].code,/starport.py urls/);
});
test('stored state cannot inject code, paths or unbounded completed steps',()=>{
 const x=normalize({a:'<script>',b:'windows',step:100,done:[0,0,1,'2',-1,8],token:'private'});
 assert.deepEqual(x.done,[0,1]);assert.equal(x.a,'linux');assert.equal(x.step,0);assert.equal(x.token,undefined);
});
test('every copied skill prompt refers to an actual repository skill',()=>{
 for(const [key] of skills){
  const p=path.join(__dirname,'../skills/starport-'+key+'/SKILL.md');assert.ok(fs.existsSync(p));
  assert.ok(agentPrompt(key,{a:'mac',b:'windows',startupA:false}).includes('skills/starport-'+key+'/SKILL.md'));
 }
});
test('tutorial guidance links do not include credential inputs or private endpoints',()=>{
 for(const step of guide({})){for(const [,url] of step.links||[])assert.ok(url.startsWith('https://'));
  for(const block of step.blocks||[])assert.ok(!block.code.includes('StrictHostKeyChecking=no'));
 }
});
