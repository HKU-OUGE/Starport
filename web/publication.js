const stageNames={not_configured:'尚未配置',planned:'待发布',access_ready:'Access 已配置',configured:'已发布',removing:'撤回中',removed:'已撤回'};
const errors={cloudflared_update_required:'请升级 cloudflared 到 2025.4.0 或更新版本。',cloudflared_unavailable:'无法运行 cloudflared，请检查安装。',hostname_changed_preview_again:'域名已被占用或变更，请核对后重建草案。',cloudflared_install_required:'请先在 A 安装 cloudflared，然后继续发布。',access_dependency_required:'请更新服务端 Python 依赖，再继续。',console_origin_unavailable:'会话控制台暂时不可达，请先恢复本地会话入口。',configure_zero_trust_team_first:'请先在 Cloudflare 开通 Zero Trust 团队。',configure_access_login_method_first:'请先配置 Access 登录方式，例如邮箱验证码。',cloudflare_api_token_required:'请填写有效的 Cloudflare API Token。',exact_zone_not_available:'Token 无法读取这个域名，请检查域名及 Zone 权限。',cloudflare_http_401:'Cloudflare 拒绝凭证，请更新 API Token。',cloudflare_http_403:'Cloudflare 权限不足，请按指南检查 Token 范围。',cloudflare_http_429:'Cloudflare 暂时限流，请稍后核对状态。',cloudflare_unreachable:'暂时无法连接 Cloudflare，请稍后核对状态。',cloudflare_outcome_unknown:'请求结果不确定，请先核对云端状态，系统不会重复创建。',cloudflare_outcome_unknown_reconcile:'创建结果尚未确认，请核对云端；不会重复创建资源。',connector_exited:'连接程序已退出；已选择自动恢复时将重试。',publication_changed_preview_again:'发布计划已变化，请刷新后重新确认。',publication_port_in_use:'专用入口端口已被占用，请按指南调整。',publication_operation_failed:'操作未完成，请核对本机依赖与服务状态。'};
export function drawPublication(main,{api,el,append,button,link,modal,stamp}){
 const box=el('section',null,'publication');main.append(box);let timer,stale=false;const draft={zone:'',prefix:'console',emails:''};
 function alive(){return box.isConnected&&!stale;}
 async function load(){if(!alive())return;try{const data=await api('/api/publication');if(alive())render(data);}catch(e){if(alive())box.replaceChildren(el('p',e.message,'error'));}}
 async function act(action,data){await api('/api/publication/'+action,data);await load();}
 function render(s){
  clearTimeout(timer);box.replaceChildren();
  const active=!['idle','failed'].includes(s.operation.phase),planned=s.stage==='planned',removed=s.stage==='removed',has=!!s.deployment;
  const title=el('div',null,'card publication-state');append(title,el('h2',stageNames[s.stage]||s.stage),el('p',s.url||'把会话控制台发布到自己的 HTTPS 域名。','publication-url'));
  if(s.url&&!removed&&s.stage==='configured')title.append(link('打开远程控制台 ↗',s.url));
  const facts=el('dl',null,'publication-facts');for(const [k,v] of [['Access',s.resources.app?'已创建':'未创建'],['本机连接',s.connector_running?'运行中':'未运行'],['上次云端核对',(({healthy:'在线',degraded:'部分连接异常',inactive:'尚未连接',down:'离线',unchecked:'尚未核对'})[s.edge_status]||s.edge_status)+(s.checked_at?' · '+stamp(s.checked_at):'')],['登录验收',s.last_authenticated_request?'已验证身份请求 · '+stamp(s.last_authenticated_request):'等待在远程地址完成登录']])append(facts,el('dt',k),el('dd',v));title.append(facts);box.append(title);
  if(active)box.append(el('p','正在处理：'+({plan:'检查权限与可用域名',apply:'创建并核验 Access、Tunnel 和 DNS',refresh:'核对已有资源',start:'启动连接',stop:'停止连接',remove:'撤回本次资源',token:'更新凭证',discard:'清除草案',startup:'保存自动恢复选项'})[s.operation.phase],'notice'));
  if(s.operation.error)box.append(el('p',errors[s.operation.error]||s.operation.error,'error'));
  if(!s.connector_available||!s.jwt_available){const d=el('div',null,'notice');d.append(el('p',!s.connector_available?'需要在 A 安装 cloudflared。':'需要更新服务端 Python 依赖。'));d.append(link('安装方法与 Token 权限','https://github.com/HKU-OUGE/Starport/blob/main/docs/REMOTE.md'));box.append(d);}
  if(!has){
   const form=el('form',null,'card form-grid');
   function field(label,type,value,placeholder){const l=el('label',label),i=el(type==='textarea'?'textarea':'input');if(type!=='textarea')i.type=type;i.value=value||'';i.placeholder=placeholder||'';i.required=true;if(type==='password')i.autocomplete='off';l.append(i);form.append(l);return i;}
   const zone=field('Cloudflare 域名','text',draft.zone,'example.com'),prefix=field('域名前缀','text',draft.prefix,'console'),emails=field('允许登录的邮箱','textarea',draft.emails,'每行一个邮箱'),token=field('API Token','password','','仅保存在 A');
   const submit=el('button','检查并预览','primary');submit.type='submit';submit.disabled=active;form.append(submit);
   for(const [key,input] of Object.entries({zone,prefix,emails}))input.oninput=()=>draft[key]=input.value;
   form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;try{const payload={zone:zone.value,prefix:prefix.value,emails:emails.value,token:token.value};await api('/api/publication/plan',payload);token.value='';await load();}catch(err){token.value='';box.prepend(el('p',err.message,'error'));submit.disabled=false;}};box.append(form);
  }else{
   const actions=el('div',null,'actions'),check=el('input');check.type='checkbox';check.checked=s.resume_with_panel;check.disabled=active;if(s.stage==='configured')check.onchange=async()=>{try{await act('startup',{deployment:s.deployment,enabled:check.checked});}catch(e){box.prepend(el('p',e.message,'error'));}};
   const startup=el('label',null,'publication-startup');append(startup,check,el('span','随 Starport 管理面板自动恢复入口'));
   if(!removed)box.append(startup);
   function operation(label,action,confirm){const b=button(label,async()=>{const payload={deployment:s.deployment,resume_with_panel:check.checked};if(confirm)modal(label,append(el('div'),el('p',confirm)),()=>act(action,payload));else try{await act(action,payload);}catch(e){box.prepend(el('p',e.message,'error'));}});b.disabled=active;actions.append(b);return b;}
   if(!removed&&s.stage!=='removing'){
    if(s.stage!=='configured'){const b=operation('发布并启动','apply','将为 '+s.hostname+' 创建专用 Access 应用、邮箱策略、Tunnel 和 DNS。只允许：'+s.emails.join('、')+'。');b.classList.add('primary');}
    else if(!s.connector_running)operation('启动入口','start');
    if(s.connector_running)operation('停止入口','stop','只停止本次远程入口，本地会话和训练继续运行。');
    operation('核对云端状态','refresh');
   }
   if(planned&&!Object.keys(s.resources).length&&!s.pending||removed)operation('清除草案','discard','清除本机这次发布记录，以便重新填写。');
   else operation('撤回本次发布','remove','只删除本次创建且仍匹配的域名记录、Tunnel、Access 应用与策略。不会删除会话或其他入口。');
   box.append(actions);
   if(!removed){const details=el('details',null,'card details');details.append(el('summary','配置与凭证'));details.append(el('p','允许登录：'+s.emails.join('、')));const form=el('form',null,'actions'),label=el('label','更换 API Token'),token=el('input');token.type='password';token.autocomplete='off';token.required=true;label.append(token);const b=el('button','更新凭证');b.type='submit';b.disabled=active;append(form,label,b);form.onsubmit=async e=>{e.preventDefault();try{await api('/api/publication/token',{deployment:s.deployment,token:token.value});token.value='';await load();}catch(err){token.value='';box.prepend(el('p',err.message,'error'));}};details.append(form);box.append(details);}
  }
  const guide=el('details',null,'details');append(guide,el('summary','使用指引'),el('p','填写已接入 Cloudflare 的域名。前缀被占用时自动选择空闲后缀；先预览，再发布。登录后仍需控制台访问码。'),link('查看安装、权限与恢复步骤','https://github.com/HKU-OUGE/Starport/blob/main/docs/REMOTE.md'));box.append(guide);
  if(active)timer=setTimeout(load,1500);
 }
 load();return()=>{stale=true;clearTimeout(timer);};
}
