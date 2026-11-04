// Read recovery never retries a mutation or changes its operation identifier.
export const requestMessages={
 access_login_required:'远程访问登录已过期，请重新打开页面完成 Cloudflare 验证。',
 access_check_required:'远程入口要求重新验证访问，请重新打开页面。',
 console_read_timeout:'读取超时，正在重试。已有会话和训练继续运行。',
 console_connection_unavailable:'当前网络暂时无法读取控制台，请重试。',
 invalid_api_response:'控制台返回了无法识别的内容，请刷新重试。',
 invalid_session_list:'暂时未取得有效的会话列表，正在重试。',
 session_list_waiting:'会话列表仍在同步。可以重试读取，或重新打开页面。'
};

export function requestDeadline(path,mutation){
 if(mutation)return 80000;
 if(path==='/api/me')return 10000;
 return /^\/api\/(?:sessions|status)(?:\?|$)|^\/api\/(?:session|activity)\//.test(path)?12000:80000;
}

export function createConsoleApi({getCsrf=()=>'',onExpired=()=>{},messages={},fetcher=(...a)=>fetch(...a),schedule=setTimeout,cancel=clearTimeout}={}){
 const failure=(code,status,message)=>Object.assign(Error(message||messages[code]||requestMessages[code]||code||'请求失败'),{code,httpStatus:status});
 return async function api(path,data,options={}){
  const controller=new AbortController(),abort=()=>controller.abort();let timedOut=false;
  if(options.signal?.aborted)controller.abort();
  options.signal?.addEventListener('abort',abort,{once:true});
  const timer=schedule(()=>{timedOut=true;controller.abort();},requestDeadline(path,data!==undefined));
  try{
   const response=await fetcher(path,{signal:controller.signal,credentials:'same-origin',cache:'no-store',redirect:'manual',
    headers:data!==undefined?{'Content-Type':'application/json','X-Console-CSRF':getCsrf()}:{},
    method:data!==undefined?'POST':'GET',body:data!==undefined?JSON.stringify(data):undefined});
   if(response.type==='opaqueredirect'||response.status>=300&&response.status<400)throw failure('access_login_required');
   if(!String(response.headers.get('Content-Type')||'').toLowerCase().includes('application/json')){
    throw failure([401,403].includes(response.status)?'access_check_required':'invalid_api_response',response.status);
   }
   let value;
   try{value=await response.json();}catch(error){if(controller.signal.aborted)throw error;throw failure('invalid_api_response',response.status);}
   if(!value||typeof value!=='object'||Array.isArray(value))throw failure('invalid_api_response',response.status);
   if(!response.ok){
    if(path!='/api/login'&&(response.status===401||value.error==='invalid_session'))onExpired();
    throw failure(value.error||'invalid_api_response',response.status);
   }
   return value;
  }catch(error){
   if(options.signal?.aborted)throw error;
   if(timedOut)throw failure(data!==undefined?'connection_timeout_outcome_unknown':'console_read_timeout',undefined,
    data!==undefined?'请求超时，发送结果尚未确认，请先查看记录，不要重复发送。':undefined);
   if(typeof error.code==='string')throw error;
   throw failure('console_connection_unavailable');
  }finally{cancel(timer);options.signal?.removeEventListener('abort',abort);}
 };
}

export function validateSessionList(value){
 if(!value||typeof value!=='object'||(!Array.isArray(value.sessions)&&value.loading!==true)){
  throw Object.assign(Error(requestMessages.invalid_session_list),{code:'invalid_session_list'});
 }
 return value;
}

export function subscribeReadRecovery(refresh,doc=document,win=window){
 let last=-Infinity;
 const resume=()=>{if(doc.visibilityState==='hidden'||Date.now()-last<500)return;last=Date.now();refresh();};
 doc.addEventListener('visibilitychange',resume);
 win.addEventListener('pageshow',resume);
 win.addEventListener('online',resume);
 return()=>{doc.removeEventListener('visibilitychange',resume);win.removeEventListener('pageshow',resume);win.removeEventListener('online',resume);};
}
