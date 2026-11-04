import React,{memo,useMemo} from 'react';
import {unified} from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import {markdownUrl,activateMarkdownLink} from './markdownLinks.js';
const parser=unified().use(remarkParse).use(remarkGfm),h=React.createElement;

// Text, links, code and tables never wait for highlighting or diagram engines.
export const BasicMarkdown=memo(function BasicMarkdown(props){
 const {text='',attachments=[],session}=props;
 const tree=useMemo(()=>parser.parse(text||''),[text]);
 const definitions=new Map(tree.children.filter(n=>n.type==='definition').map(n=>[n.identifier,n]));
 function render(node,key,header=false){
  const children=()=>node.children?.map((n,i)=>render(n,key+'-'+i,header));
  const tag=(name,extra={})=>h(name,{key,...extra},children());
  switch(node.type){
   case 'text':return node.value;
   case 'html':case 'definition':return null;
   case 'paragraph':return tag('p');
   case 'heading':return tag('h'+Math.max(1,Math.min(6,node.depth)));
   case 'strong':return tag('strong');
   case 'emphasis':return tag('em');
   case 'delete':return tag('del');
   case 'inlineCode':return h('code',{key},node.value);
   case 'code':return h('pre',{key},h('code',null,node.value));
   case 'blockquote':return tag('blockquote');
   case 'break':return h('br',{key});
   case 'thematicBreak':return h('hr',{key});
   case 'list':return tag(node.ordered?'ol':'ul',node.ordered?{start:node.start||1}:{});
   case 'listItem':return h('li',{key},typeof node.checked==='boolean'?h('input',{type:'checkbox',checked:node.checked,disabled:true,readOnly:true}):null,children());
   case 'table':return h('div',{key,className:'table-scroll'},h('table',null,h('thead',null,render(node.children[0],key+'-head',true)),h('tbody',null,node.children.slice(1).map((n,i)=>render(n,key+'-'+i)))));
   case 'tableRow':return tag('tr');
   case 'tableCell':return tag(header?'th':'td');
   case 'link':case 'linkReference':{
    const href=markdownUrl(node.url||definitions.get(node.identifier)?.url,attachments,session);
    return href?h('a',{key,href,target:'_blank',rel:'noreferrer',onClick:e=>activateMarkdownLink(e,href,props)},children()):h(React.Fragment,{key},children());
   }
   case 'image':case 'imageReference':{
    const href=markdownUrl(node.url||definitions.get(node.identifier)?.url,attachments,session);
    return href?h('a',{key,href,target:'_blank',rel:'noreferrer'},node.alt||'查看图片'):h('span',{key},node.alt||'图片见附件');
   }
   case 'footnoteReference':return h('sup',{key},node.label||node.identifier);
   case 'footnoteDefinition':return h('div',{key,className:'footnote'},node.label||node.identifier,' ',children());
   default:return node.children?h(React.Fragment,{key},children()):String(node.value||'');
  }
 }
 return h('div',{className:'markdown basic-markdown'},tree.children.map((n,i)=>render(n,String(i))));
});
