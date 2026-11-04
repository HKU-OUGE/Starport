// One optional renderer download is shared by all messages and sessions.
export function createRichTextLoader(importer,{schedule=setTimeout,cancel=clearTimeout}={}){
 let state={phase:'idle',component:null,version:0},pending=null;const listeners=new Set();
 const publish=next=>{state={...state,...next};for(const listener of listeners)listener();};
 function load(){
  if(pending||state.component)return pending||Promise.resolve(state.component);
  publish({phase:'loading',version:state.version+1});
  const timer=schedule(()=>publish({phase:'slow'}),12000);
  pending=Promise.resolve().then(importer).then(module=>{
   cancel(timer);pending=null;publish({phase:'ready',component:module.default});return module.default;
  },()=>{cancel(timer);pending=null;publish({phase:'error',component:null});return null;});
  return pending;
 }
 return {load,getSnapshot:()=>state,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn);},
  renderFailed:()=>{if(state.phase!=='error')publish({phase:'error',component:null});}};
}
export function richModuleUrl(value,base,attempt){
 const url=new URL(value,base),origin=new URL(base).origin;
 if(url.origin!==origin||url.username||url.password||!(/^\/assets\/[A-Za-z0-9_-]+\.js$/.test(url.pathname)||url.pathname==='/src/RichMarkdown.jsx'))throw Error('invalid_renderer_url');
 url.searchParams.set('_rich_retry',String(attempt));return url.href;
}
let attempt=0;
export const richText=createRichTextLoader(()=>{
 const entry=document.querySelector('meta[name="console-rich-module"]')?.content;
 if(!entry)throw Error('renderer_not_configured');
 // A new URL retries a failed browser module fetch without reloading the chat.
 return import(/* @vite-ignore */ richModuleUrl(entry,window.location.href,++attempt));
});
