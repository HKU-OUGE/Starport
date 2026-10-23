const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element {
 constructor(tag,text=''){this.tag=tag;this.children=[];this.attrs={};this.textContent=text;}
 append(...values){this.children.push(...values);}
 setAttribute(k,v){this.attrs[k]=v;}
}
const el=(tag,text,cls)=>new Element(tag,text==null?'':String(text));
const append=(p,...children)=>{p.append(...children.filter(Boolean));return p;};
const document={createTextNode:t=>new Element('#text',t)};
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const context={el,append,document,button:(text,fn)=>new Element('button',text)};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function inlineMarkdown('),source.indexOf('async function drawDocs(')),context);
function descendants(n){return[n,...n.children.flatMap(descendants)];}
function text(n){return n.textContent+n.children.map(text).join('');}
test('raw HTML and javascript links remain inert text',()=>{
 const d=context.markdownDocument('# Heading\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n![x](x)');
 const all=descendants(d);
 assert.equal(all.some(n=>['script','img','iframe'].includes(n.tag)),false);
 assert.equal(all.some(n=>n.tag==='a'&&String(n.href).startsWith('javascript:')),false);
 assert.match(text(d),/<script>alert\(1\)<\/script>/);
});
test('tables, inline code, headings and lists render as semantic nodes',()=>{
 const d=context.markdownDocument('# Guide\n\n## Install\n\n- Use `starport`\n- Read **status**\n\n| Item | Value |\n| --- | --- |\n| CPU | 4 |\n\n```sh\necho example\n```');
 const all=descendants(d);
 for(const tag of ['h1','h2','ul','li','code','strong','table','thead','tbody','pre'])assert.ok(all.some(n=>n.tag===tag),tag);
 assert.equal(all.filter(n=>n.tag==='td').length,2);
 assert.equal(all.filter(n=>n.tag==='a').length,2);
 assert.match(text(d),/echo example/);
});
test('every shipped guide has a title, a contents list and no HTML nodes',()=>{
 const dir=path.join(__dirname,'../docs');
 for(const name of ['GUIDE','SERVICES','NETWORK','INSTALL','SECURITY']){
  const nodes=descendants(context.markdownDocument(fs.readFileSync(path.join(dir,name+'.md'),'utf8')));
  assert.ok(nodes.some(n=>n.tag==='h1'),name);
  assert.ok(nodes.some(n=>n.tag==='nav'),name);
  assert.equal(nodes.some(n=>n.tag==='script'),false);
 }
});
