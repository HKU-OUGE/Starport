/* Classic script: runs before the application module, including on older phones. */
(function () {
 'use strict';
 var timer, completed=false, checking=false, reloadScheduled=false;
 var messages={
  startup_slow:'网页脚本仍未启动，正在检查连接。',
  startup_access_required:'远程入口需要重新登录，请重新打开页面完成验证。',
  startup_network_failed:'网页文件读取失败，请检查手机网络后重新打开。',
  startup_asset_missing:'网页文件暂时未取得，请重新打开以获取当前版本。',
  startup_invalid_asset:'网页文件返回了登录页或异常内容，请重新打开。',
  startup_syntax_error:'网页脚本出现语法错误。请重新打开；若仍失败，可尝试更新后的 Chrome 并反馈下方状态码。',
  startup_execution_error:'网页脚本启动失败，请重新打开；若仍失败，请反馈下方状态码。',
  startup_module_failed:'网页模块未能加载，请重新打开；若仍失败，请反馈下方状态码。',
  startup_pending:'连接正常，但网页脚本尚未启动。可重新打开，或尝试更新后的 Chrome。'
 };
 function shell(){return document.getElementById('console-bootstrap');}
 function retryUrl(){var url=new URL(window.location.href);url.searchParams.set('_reload',String(Date.now()));return url.href;}
 function show(code){
  if(completed||!shell())return;
  var status=document.getElementById('console-startup-status'),detail=document.getElementById('console-startup-code'),retry=document.getElementById('console-startup-retry');
  if(status)status.textContent=messages[code]||messages.startup_execution_error;
  if(detail)detail.textContent='状态码：'+code;
  if(retry)retry.href=retryUrl();
 }
 function autoReload(){
  if(reloadScheduled||!shell())return;
  try{
   var now=Date.now(),last=Number(sessionStorage.getItem('console-startup-reload')||0);
   if(now-last<120000)return;
   sessionStorage.setItem('console-startup-reload',String(now));
  }catch(_){return;}
  reloadScheduled=true;
  var detail=document.getElementById('console-startup-code');
  if(detail)detail.textContent+=' · 正在重新读取一次';
  setTimeout(function(){if(!completed&&shell())window.location.replace(retryUrl());},1000);
 }
 function read(url){
  if(typeof fetch!=='function'||typeof AbortController!=='function')return Promise.reject(Error('unsupported_browser'));
  var controller=new AbortController(),deadline=setTimeout(function(){controller.abort();},6000);
  return fetch(url,{method:'HEAD',redirect:'manual',credentials:'same-origin',cache:'no-store',signal:controller.signal}).then(function(response){clearTimeout(deadline);return response;},function(error){clearTimeout(deadline);throw error;});
 }
 function diagnose(reason){
  if(completed||!shell()||checking)return;
  show(reason);checking=true;
  if(reason==='startup_syntax_error'||reason==='startup_execution_error'){checking=false;return;}
  read('/health').then(function(response){
   if(response.type==='opaqueredirect'||response.status===401||response.status===403)return 'startup_access_required';
   if(!response.ok)return 'startup_network_failed';
   var entry=document.querySelector('script[type="module"][src]');
   if(!entry)return 'startup_asset_missing';
   return read(entry.src).then(function(asset){
    if(asset.type==='opaqueredirect'||asset.status===401||asset.status===403)return 'startup_access_required';
    if(!asset.ok)return 'startup_asset_missing';
    if(!/javascript|ecmascript/.test(asset.headers.get('Content-Type')||''))return 'startup_invalid_asset';
    return reason==='startup_module_failed'?'startup_module_failed':'startup_pending';
   });
  }).catch(function(){return 'startup_network_failed';}).then(function(code){
   checking=false;show(code);
   // A single reload before React mounts cannot resend a user message.
   if(reason==='startup_module_failed'&&code!=='startup_pending')autoReload();
  });
 }
 window.addEventListener('console-startup-ready',function(){completed=true;clearTimeout(timer);});
 window.addEventListener('console-startup-failed',function(event){diagnose(event.detail&&event.detail.code||'startup_module_failed');});
 window.addEventListener('error',function(event){
  if(!shell())return;
  if(event.target&&event.target.tagName==='SCRIPT')diagnose('startup_module_failed');
  else if(event.error)diagnose(event.error.name==='SyntaxError'?'startup_syntax_error':'startup_execution_error');
 },true);
 window.addEventListener('unhandledrejection',function(){if(shell())diagnose('startup_execution_error');});
 function begin(){var retry=document.getElementById('console-startup-retry');if(retry)retry.href=retryUrl();timer=setTimeout(function(){diagnose('startup_slow');},12000);}
 // Module downloads can delay DOMContentLoaded; start the watchdog immediately.
 begin();
})();
