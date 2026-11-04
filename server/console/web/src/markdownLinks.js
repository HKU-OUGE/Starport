import {registeredReference} from './documentFiles.js';
export const mediaUrl=(session,item)=>`/api/${item.source==='document'?'documents':'media'}/${encodeURIComponent(session)}/${item.id}`;
export const artifactLink=value=>/^https:\/\/claude\.ai\/(?:code\/)?artifact\/[A-Za-z0-9_-]{12,80}(?:[?#].*)?$/.test(value||'');
export function markdownUrl(value,attachments,session){
 const urls=new Map(attachments.filter(a=>a.available&&a.reference).map(a=>[a.reference,mediaUrl(session,a)]));
 return registeredReference(urls,value)||(/^(https?:\/\/|mailto:|#)/i.test(value||'')?value:'');
}
export function activateMarkdownLink(event,href,{attachments=[],session,onArtifact,onPdf}){
 if(event.metaKey||event.ctrlKey)return;
 const pdf=attachments.find(a=>a.available&&a.mime==='application/pdf'&&mediaUrl(session,a)===href);
 if(pdf&&onPdf){event.preventDefault();onPdf(pdf);}
 else if(onArtifact&&artifactLink(href)){event.preventDefault();onArtifact(href);}
}
