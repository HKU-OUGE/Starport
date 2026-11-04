(function () {
  'use strict';
  const OS = {linux:'Linux',mac:'macOS',windows:'Windows'};
  const defaults = {a:'linux',b:'mac',startupA:true,startupB:true,step:0,done:[]};
  const docs = name => 'https://github.com/HKU-OUGE/Starport/blob/main/docs/'+name+'.md';
  const skills = [
    ['onboard','从零搭建','两台电脑、角色分工与完整验收'],
    ['starship','客户端与 Mac','软件、权限、锁屏与后台服务'],
    ['pair','SSH 配对','专用公钥、主机指纹、受限接口'],
    ['network','网络与订阅','独立网关、主备节点、断网检查'],
    ['remote','手机远程访问','VPN、Cloudflare Access 与 Tunnel'],
    ['compute','接入算力','已有计算节点、依赖和共享数据'],
    ['maintain','维护与卸载','定位故障、安全更新、保留数据']
  ];
  function normalize(value) {
    const v=value&&typeof value==='object'?value:{};
    return {a:OS[v.a]?v.a:defaults.a,b:OS[v.b]?v.b:defaults.b,
      startupA:typeof v.startupA==='boolean'?v.startupA:true,
      startupB:typeof v.startupB==='boolean'?v.startupB:true,
      step:Number.isInteger(v.step)&&v.step>=0&&v.step<8?v.step:0,
      done:Array.isArray(v.done)?[...new Set(v.done.filter(x=>Number.isInteger(x)&&x>=0&&x<8))]:[]};
  }
  function guide(selection) {
    const s=normalize(selection),a=s.a==='windows'?'py -3':'python3',b=s.b==='windows'?'py -3':'python3';
    const clientFlag=s.b==='windows'?' --client-platform windows':'';
    const installA=a+' setup.py server --ssh-host starship'+clientFlag+(s.startupA?'':' --no-register');
    const installB=b+' setup.py client'+(s.startupB?'':' --no-register');
    const block=(label,code)=>({label,code});
    return [
      {title:'准备软件与仓库',short:'准备',place:'A + B',text:'在两台电脑下载 Starport。A 需要 Python、Node.js、npm 和 OpenSSH；B 需要 Python、Claude Code 和 SSH 服务。',
       blocks:[block('两台电脑 · 已安装 Git 时','git clone https://github.com/HKU-OUGE/Starport.git\ncd Starport'),block('A · '+OS[s.a],a+' tools/preflight.py server'),block('B · '+OS[s.b],b+' tools/preflight.py client')],
       hint:'没有 Git？使用 GitHub 的 Code → Download ZIP。Python、Node 安装后重新打开终端；Node.js 要求 22.12+。',
       links:[['软件下载与版本','https://nodejs.org/en/download'],['Python','https://www.python.org/downloads/'],['完整准备清单',docs('QUICKSTART')]],
       details:'preflight 只读取软件状态，不会安装程序、修改网卡或验证账号。B 的 Claude 尚未安装时可以先记录缺项，下一步先确定网络。'},
      {title:'确定两台电脑的网络',short:'网络',place:'先在 A 规划',text:'A 需要能通过预定私有路径连接 B。如果 B 只能走指定代理，先建立独立网关，再登录 Claude。',
       bullets:['只使用会话工作台：可沿用已有可信局域网。','严格网络隔离：B → Linux／OpenWrt 网关 → 指定代理。网关可运行在 A 的虚拟机里。','检查代理全断时 B 是否断网，并排除第二网卡、Wi-Fi、IPv6 和主机共享网络旁路。'],
       hint:'安装 Starport 不会自动创建路由虚拟机或设置防火墙。需要隔离时，应先让 Agent 按真实网口准备并验收网络。',
       links:[['网络配置与验收',docs('NETWORK')]],details:'Windows、macOS 上的控制台可以原生运行；承担路由防护的组件需要 Linux／OpenWrt。不要将“装好了网页”当作“已实现网络隔离”。'},
      {title:'让 B 跑起 Starship',short:'客户端',place:'电脑 B · '+OS[s.b],text:'在 B 安装 Claude Code 并完成本机登录，然后安装 Starship。账号凭据留在 B。',
       blocks:s.startupB?[block('B · 安装与检查',b+' setup.py client --check\n'+installB+'\n'+b+' starship.py status')]:[block('B · 安装',b+' setup.py client --check\n'+installB),block('B · 手动启动，保持此终端打开',b+' tools/run_services.py client'),block('B · 另开终端检查',b+' starship.py status')],
       hint:s.startupB?'当前选择：用户登录后自动启动。安装器会再次确认。锁屏会保留服务，退出登录或睡眠另行验收。':'当前选择：手动运行。保持 run_services 终端打开，另开终端检查 status。此选项不会取消已有的自启注册。',
       links:[['Claude 官方安装','https://code.claude.com/docs/en/setup'],[s.b==='mac'?'新 Mac 清单':'客户端说明',docs(s.b==='mac'?'MAC':'INSTALL')]],
       details:'后台服务会记录并查找 Claude 启动位置，不依赖交互终端 PATH。基本会话控制不需要屏幕录制、辅助功能、SMB 或 VNC。'},
      {title:'用专用 SSH 密钥配对',short:'配对',place:'A 生成密钥，B 只接收公钥',text:'先启用 B 的 SSH 服务。在 B 本机核对主机指纹，A 才能信任这台设备。占位符需要换成你的实际值。',
       blocks:[block('A · 生成登录密钥',s.a==='windows'?'ssh-keygen -t ed25519 -f "$env:USERPROFILE\\.ssh\\starport_starship" -C starport':'ssh-keygen -t ed25519 -f ~/.ssh/starport_starship -C starport'),
       block('B · 添加复制过来的 .pub 公钥',b+' tools/pair.py authorize --public-key starport_starship.pub\n'+b+' tools/pair.py authorize --public-key starport_starship.pub --apply'),
       block('A · 核对并固定 B 的主机指纹',a+' tools/pair.py pin --host DEVICE_IP --fingerprint HOST_FINGERPRINT --apply\n'+a+' tools/pair.py config --host DEVICE_IP --user DEVICE_USER')],
       hint:'只把 .pub 公钥复制到 B。最后的 Host starship 配置段合并到 A 的 SSH config；不要覆盖其他连接。',links:[['指纹怎么取？配置放哪里？',docs('PAIRING')]],
       details:'默认只允许固定 RPC 命令，不能用配对密钥打开普通 shell。Windows B 建议用非管理员本地用户，避免管理员共用公钥文件。'},
      {title:'在 A 打开工作台',short:'服务端',place:'电脑 A · '+OS[s.a],text:'先确认 RPC 配对成功，再安装网页控制台和管理面板。访问码在 A 本机读取。',
       blocks:s.startupA?[block('A · 验证与安装',a+' tools/pair.py verify'+clientFlag+'\n'+a+' setup.py server --check\n'+installA+'\n'+a+' starport.py urls\n'+a+' starport.py key')]:[block('A · 验证与安装',a+' tools/pair.py verify'+clientFlag+'\n'+a+' setup.py server --check\n'+installA),block('A · 手动启动，保持此终端打开',a+' tools/run_services.py server'),block('A · 另开终端读取入口',a+' starport.py urls\n'+a+' starport.py key')],
       hint:s.startupA?'默认会话入口 18881，管理面板 18882，均仅监听本机。以 urls 的实际输出为准。':'保持 run_services 终端打开，另开终端读取 urls 和 key。没有建立新的登录启动注册。',
       links:[['安装参数说明',docs('INSTALL')]],details:'B 是 Windows 时已自动加入 --client-platform windows。浏览器访问码与 Claude 账号是两个独立登录步骤。'},
      {title:'完成第一次会话验收',short:'验收',place:'浏览器 + B',text:'用最小任务确认消息、文件和锁屏行为，再开始正式工作。',
       bullets:['能列出 B 上的项目与会话；新项目放在 B 用户目录下。','选一个空闲会话，明确授权后发送一次简单测试，核对回复。','上传小图片或文本，确认附件可读，返回文件可下载。','锁屏 B 后再读一次；重启后登录，再检查已选择的自启。'],
       blocks:[block('A · 只读连接检查',a+' tools/pair.py verify'+clientFlag)],
       hint:'原生 Desktop 会话要开启 Remote Control。独立后台会话不会自动合并进 Desktop 同一条历史。',links:[['会话与附件',docs('CONSOLE')]],details:'完成勾选是你手动记录的结果，网页不会代替真实设备验收。未做过的锁屏、重启或网络故障检查请保留为待验证。'},
      {title:'再接手机和算力',short:'扩展',place:'按需在 A 配置',text:'工作台跑通后，再增加远程入口或已有计算节点。它们不是第一次收发消息的前提。',
       bullets:['手机：已有 VPN，或 Cloudflare Access + Tunnel。','Cloudflare：绑定自己的域名，按邮箱授权，源站指向 A 的回环 18883。','算力：接入已有隔离执行器，复用只读依赖与数据；先验收小任务。'],
       links:[['手机访问',docs('REMOTE')],['计算节点',docs('SERVICES')]],
       details:'同一控制台的 Access 邮箱白名单不等于按会话分权。不同人的独立空间应使用独立部署、状态目录和域名。仓库不会自动创建付费云实例。'},
      {title:'留下可维护的配置',short:'维护',place:'A + B',text:'记录入口、服务名称和配置位置。更新前先确认当前没有会被打断的会话。',
       blocks:[block('A · 常用检查',a+' starport.py status\n'+a+' starport.py services'),block('B · 常用检查',b+' starship.py status')],
       bullets:['配置和密钥在仓库外备份，项目与历史默认保留。','升级时保留原来的登录启动选择；不覆盖自己的未提交改动。','卸载只移除选定角色的服务，不停止路由器或远程训练。'],
       links:[['更新、排障与卸载',docs('MAINTENANCE')],['Agent 技能列表',docs('SKILLS')]],details:'空间清理先列出具体文件、确认传输完成和正式副本，再决定删除。不要仅按文件年龄清空整个状态目录。'}
    ];
  }
  function agentPrompt(key,selection) {
    const s=normalize(selection);
    return `请阅读当前 Starport 仓库的 AGENTS.md 和 skills/starport-${key}/SKILL.md。\n电脑 A 使用 ${OS[s.a]}，电脑 B 使用 ${OS[s.b]}。\nA ${s.startupA?'希望':'不希望'}注册登录启动，B ${s.startupB?'希望':'不希望'}注册登录启动。\n请先检查现状，再完成已授权的安装、配置和验收；已有服务和活动任务请保留。账号登录、权限窗口和主机指纹由我在设备上确认。缺少信息时只询问下一步必需的内容。`;
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={normalize,guide,agentPrompt,skills};
  if(typeof document==='undefined')return;
  const storage='starport-onboarding-v1';let selection=normalize(null),noticeTimer;
  try{selection=normalize(JSON.parse(localStorage.getItem(storage)));}catch{}
  const $=id=>document.getElementById(id);
  function save(){try{localStorage.setItem(storage,JSON.stringify(selection));}catch{}}
  function element(tag,text,cls){const x=document.createElement(tag);if(text!=null)x.textContent=text;if(cls)x.className=cls;return x;}
  function notify(text){$('notice').textContent=text;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('notice').textContent='',3500);}
  async function copy(text){try{if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(text);else{const field=element('textarea');field.value=text;document.body.append(field);field.select();const ok=document.execCommand('copy');field.remove();if(!ok)throw Error();}notify('已复制，可以粘贴到终端或 Agent。');}catch{notify('浏览器未允许复制，请直接选中文本复制。');}}
  function button(text,fn,cls){const b=element('button',text,cls);b.type='button';b.onclick=fn;return b;}
  function jump(i){selection.step=Math.max(0,Math.min(7,i));save();render();}
  function render(){
    const steps=guide(selection),item=steps[selection.step];
    $('progress').value=selection.done.length;$('progress-label').textContent=`已勾选 ${selection.done.length} / 8 步`;
    $('steps').replaceChildren(...steps.map((s,i)=>{const b=button('',()=>jump(i),selection.done.includes(i)?'done':'');b.append(element('span',selection.done.includes(i)?'✓':String(i+1).padStart(2,'0'),'number'),element('span',s.short));if(selection.step===i)b.setAttribute('aria-current','step');return b;}));
    const target=$('step-content');target.replaceChildren(element('div',`0${selection.step+1} / ${item.place}`,'step-kicker'),element('h3',item.title),element('p',item.text));
    if(item.bullets){const ul=element('ul');item.bullets.forEach(t=>ul.append(element('li',t)));target.append(ul);}
    for(const block of item.blocks||[]){const wrap=element('div',null,'code'),head=element('div',null,'code-head');head.append(element('span',block.label),button('复制',()=>copy(block.code)));wrap.append(head,element('pre',block.code));target.append(wrap);}
    if(item.hint)target.append(element('div',item.hint,'hint'));
    const links=element('div',null,'step-links');for(const [label,url] of item.links||[]){const a=element('a',label+' ↗');a.href=url;a.target='_blank';a.rel='noopener noreferrer';links.append(a);}target.append(links);
    const detail=element('details');detail.append(element('summary','需要时再看'),element('p',item.details));target.append(detail);
    const bottom=element('div',null,'step-bottom'),label=element('label'),check=element('input');check.type='checkbox';check.checked=selection.done.includes(selection.step);check.onchange=()=>{selection.done=check.checked?[...new Set([...selection.done,selection.step])]:selection.done.filter(i=>i!==selection.step);save();render();};label.append(check,document.createTextNode('我已完成这一步的实际检查'));
    const controls=element('div',null,'step-controls'),prev=button('← 上一步',()=>jump(selection.step-1)),next=button(selection.step===7?'返回第一步':'下一步 →',()=>jump(selection.step===7?0:selection.step+1),'primary');prev.disabled=selection.step===0;controls.append(prev,next);bottom.append(label,controls);target.append(bottom);
  }
  $('server-os').value=selection.a;$('client-os').value=selection.b;$('startup-a').checked=selection.startupA;$('startup-b').checked=selection.startupB;
  $('choices').onsubmit=e=>e.preventDefault();
  $('choices').onchange=()=>{const next=normalize({a:$('server-os').value,b:$('client-os').value,startupA:$('startup-a').checked,startupB:$('startup-b').checked,step:selection.step,done:selection.done});if(next.a!==selection.a||next.b!==selection.b||next.startupA!==selection.startupA||next.startupB!==selection.startupB)next.done=[];selection=next;save();render();};
  $('agent-copy').onclick=()=>copy(agentPrompt('onboard',selection));
  $('reset').onclick=()=>{selection.done=[];selection.step=0;save();render();notify('进度已重置，系统选择保留。');};
  for(const [key,title,text] of skills){const row=element('div',null,'skill-row'),info=element('div');info.append(element('h3',title),element('p',text));row.append(info,button('复制指令',()=>copy(agentPrompt(key,selection))));$('skill-list').append(row);}
  render();
})();
