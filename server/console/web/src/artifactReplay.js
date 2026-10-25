import {unified} from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
const parser=unified().use(remarkParse).use(remarkGfm);
const textNode=text=>({tag:'text',text});
const contentText=n=>n.text ?? (n.children||[]).map(contentText).join('');
const href=s=>/^(https?:\/\/|mailto:|#)/i.test(s||'')?s:'';

function markdown(source, images){
 if(source?.from?.kind!=='inline'||source.as!=='markdown'||typeof source.from.content!=='string')throw Error('unsupported_source');
 const blocks=source.blocks||{};
 const raw=source.from.content.replace(/<\?claude block ([A-Za-z0-9_-]+)\?>/g,(_,key)=>`STARPORTBLOCK_${key}_END`);
 function token(key){
  const b=blocks[key];if(!b)throw Error('missing_embed_binding');
  if(b.type==='date')return textNode(b.value||'');
  if(b.type==='mention')return textNode(b.name||'');
  if(b.type==='pending')return {tag:'placeholder',text:b.intent||'Pending section'};
  if(b.type==='embed'||b.type==='media')return {tag:'figure',caption:b.caption||b.alt||'',image:images[b.ref||b.blob]||null};
  throw Error('unsupported_embed');
 }
 function convert(n,header=false){
  if(n.type==='text'){
   const parts=n.value.split(/(STARPORTBLOCK_[A-Za-z0-9_-]+_END)/g).filter(Boolean).map(s=>s.startsWith('STARPORTBLOCK_')?token(s.slice(14,-4)):textNode(s));
   return parts.length===1?parts[0]:{tag:'span',children:parts};
  }
  if(n.type==='code')return {tag:['latex','tex','math'].includes(n.lang)?'math':'pre',text:n.value};
  if(n.type==='inlineCode')return {tag:'code',children:[textNode(n.value)]};
  if(n.type==='image')return {tag:'figure',caption:n.alt||'Image',image:images[n.url]||null};
  if(n.type==='html')return textNode(n.value);
  const children=(n.children||[]).map((x,i)=>convert(x,n.type==='table'?i===0:header));
  if(n.type==='paragraph'&&children.length===1&&['figure','placeholder'].includes(children[0].tag))return children[0];
  if(n.type==='link')return {tag:href(n.url)?'a':'span',href:href(n.url),children};
  const tag={root:'article',paragraph:'p',strong:'strong',emphasis:'em',delete:'s',list:n.ordered?'ol':'ul',listItem:'li',blockquote:'blockquote',break:'br',heading:'h'+n.depth,table:'table',tableRow:'tr',tableCell:header?'th':'td',thematicBreak:'p'}[n.type];
  if(!tag)throw Error('unsupported_markdown_'+n.type);
  return {tag,children};
 }
 return convert(parser.parse(raw)).children;
}

export function replayArtifact(value,images){
 const records=value.replay;if(!Array.isArray(records)||records.length>2000)throw Error('replay_limit');
 let blocks=[],nodeId=null,revision=0,captured_at='',applied=0;
 for(const r of records){
  if(nodeId&&r.node!==nodeId)throw Error('multiple_documents');
  nodeId=r.node;
  const op=r.op,t=op.target||{},source=op.with||op.source;
  const ids=t.ids||[];
  if(op.op==='replace'&&t.kind==='find'&&source?.as==='text'&&source.from?.kind==='inline'){
   const matches=blocks.filter(b=>contentText(b.view).includes(t.text));
   if(matches.length!==1||contentText(matches[0].view).split(t.text).length!==2)throw Error('ambiguous_find');
   const target=matches[0];let done=false;
   function replace(n){if(typeof n.text==='string'&&n.text.includes(t.text)){n.text=n.text.replace(t.text,source.from.content);done=true;}for(const c of n.children||[])replace(c);}
   replace(target.view);
   if(!done)target.view={tag:target.view.tag,children:[textNode(contentText(target.view).replace(t.text,source.from.content))]};
   const ack=r.blocks.find(x=>x.id===target.id);if(ack)Object.assign(target,{hash:ack.hash,rev:r.revision});
  } else if(op.op==='replace'&&t.kind==='cell'&&Number.isInteger(t.row)&&Number.isInteger(t.col)){
   const table=blocks.find(b=>b.id===t.table);
   // Prose cell coordinates address data rows and one-based columns.
   if(table?.view.tag!=='table'||t.row<1||t.col<1||!table.view.children[0]?.children.every(c=>c.tag==='th'))throw Error('cell_coordinates');
   const cell=table.view.children[t.row]?.children[t.col-1];
   const added=markdown(source,images);if(!cell||added.length!==1)throw Error('cell_target');
   if(r.blocks.length!==1||r.blocks[0].id!==t.table||r.blocks[0].text!==contentText(added[0]))throw Error('cell_ack_mismatch');
   cell.children=added[0].tag==='p'?added[0].children:added;
   table.hash=r.blocks[0].hash;table.rev=r.revision;
  } else if(op.op==='insert'&&source?.as==='blocks'&&source.from?.kind==='inline'&&t.kind==='blocks'&&ids.length===1&&['start','end'].includes(op.side)){
   const target=blocks.find(b=>b.id===ids[0]);
   if(target?.view.tag!=='table')throw Error('structured_target');
   function structured(n){
    if(n.type==='text')return textNode(n.text||'');
    const tag={row:'tr',cell:n.header?'th':'td',paragraph:'p',bold:'strong',italic:'em',code:'code'}[n.type];
    if(!tag)throw Error('unsupported_structured_block');
    return {tag,children:(n.content||[]).map(structured)};
   }
   const added=source.from.content.map(structured);if(added.some(n=>n.tag!=='tr'))throw Error('structured_table_rows');
   if(op.side==='end')target.view.children.push(...added);else target.view.children.unshift(...added);
   const ack=r.blocks.find(x=>x.id===target.id);if(ack)Object.assign(target,{hash:ack.hash,rev:r.revision});
  } else if(op.op==='delete'){
   const matches=ids.map(id=>blocks.findIndex(b=>b.id===id));
   // Empty initial editor paragraphs are not part of inserted source content.
   if(matches.some(x=>x<0)&&op.ifHash!=='e3b0c442')throw Error('delete_target_missing');
   blocks=blocks.filter(b=>!ids.includes(b.id));
  } else if(['insert','replace'].includes(op.op)){
   const newViews=markdown(source,images);
   if(newViews.length!==r.blocks.length)throw Error('block_alignment_'+newViews.length+'_'+r.blocks.length);
   const added=newViews.map((view,i)=>({view,id:r.blocks[i].id,hash:r.blocks[i].hash,rev:r.revision}));
   if(t.kind==='root'&&op.op==='insert'){
    if(op.side==='end')blocks.push(...added);else if(op.side==='start')blocks.unshift(...added);else throw Error('root_position');
   } else if(t.kind==='blocks'&&ids.length){
    const indexes=ids.map(id=>blocks.findIndex(b=>b.id===id));
    if(indexes.some(i=>i<0)||indexes.some((x,i)=>i&&x!==indexes[0]+i))throw Error('target_missing');
    if(op.ifHash&&blocks[indexes[0]].hash!==op.ifHash)throw Error('hash_precondition');
    if(op.op==='replace')blocks.splice(indexes[0],ids.length,...added);
    else if(op.side==='after')blocks.splice(indexes.at(-1)+1,0,...added);
    else if(op.side==='before')blocks.splice(indexes[0],0,...added);
    else throw Error('block_position');
   } else throw Error('unsupported_target');
  } else throw Error('unsupported_operation');
  revision=Math.max(revision,r.revision||0);captured_at=r.captured_at;applied++;
 }
 if(!blocks.length)throw Error('empty_document');
 const content={tag:'article',children:blocks.map(b=>b.view)},toc=[],missing=[],stats={};let figures=0,available=0,stale=0;
 function walk(n){
  stats[n.tag]=(stats[n.tag]||0)+1;
  if(/^h[1-6]$/.test(n.tag)){n.id='section-'+toc.length;toc.push({id:n.id,level:Number(n.tag[1]),title:contentText(n)});}
  if(n.tag==='placeholder')throw Error('unresolved_section');
  if(n.tag==='figure'){figures++;if(n.image){available++;if(n.image.stale)stale++;}else missing.push(n.caption||'Figure');}
  for(const c of n.children||[])walk(c);
 }
 walk(content);
 return {content,toc,missing,stats,revision,captured_at,figure_count:figures,available_figures:available,stale_figures:stale,snapshot:true,reconstruction:'acknowledged_operations',operations_applied:applied};
}
