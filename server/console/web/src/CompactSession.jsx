import ModelSettings from './ModelSettings.jsx';
import React, {useCallback, useEffect, useLayoutEffect, useRef} from 'react';

export function useComposerSize(value, sessionId) {
  const ref=useRef(null);
  const resize=useCallback(()=>{
    const el=ref.current;
    if(!el)return;
    if(window.matchMedia('(max-width:760px)').matches){
      el.style.height='0px';
      el.style.height=Math.min(132,Math.max(28,el.scrollHeight))+'px';
    }else el.style.height='';
  },[]);
  useLayoutEffect(resize,[value,sessionId,resize]);
  useEffect(()=>{window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize);},[resize]);
  return ref;
}

export function SessionInfo({session, statusLabel, notice, close, refresh, stop, navigate, canStop, api, newId, onApplied}) {
  const ref=useRef(null),closeRef=useRef(close);closeRef.current=close;
  useEffect(()=>{
    const previous=document.activeElement,dialog=ref.current;
    dialog.querySelector('button')?.focus();
    const keys=event=>{
      if(event.key==='Escape'){event.preventDefault();closeRef.current();}
      if(event.key==='Tab'){
        const controls=[...dialog.querySelectorAll('button:not(:disabled),select:not(:disabled),input:not(:disabled),summary,a[href],[tabindex="0"]')].filter(el=>el.getClientRects().length);
        const first=controls[0],last=controls.at(-1);
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
      }
    };
    document.addEventListener('keydown',keys);
    return()=>{document.removeEventListener('keydown',keys);if(previous?.isConnected)previous.focus();};
  },[]);
  return <div className="scrim" onClick={close}><section className="modal compact-session-sheet" ref={ref} role="dialog" aria-modal="true" aria-label="会话详情" onClick={e=>e.stopPropagation()}>
    <header><h2>会话详情</h2><button className="icon-button" aria-label="关闭会话详情" onClick={close}>×</button></header>
    <h3 className="session-full-title">{session.title}</h3>
    <dl className="session-facts">
      {session.project&&<><dt>项目</dt><dd>{session.project}</dd></>}
      <dt>状态</dt><dd>{statusLabel}</dd>
      <dt>连接方式</dt><dd>{session.kind==='remote'?'Remote Control':session.kind==='managed'?'后台会话':'桌面历史'}</dd>
      {session.model&&<><dt>模型</dt><dd>{session.model.replace('claude-','')}</dd></>}
      {session.effort&&<><dt>推理强度</dt><dd>{session.effort}</dd></>}
    </dl>
    {notice&&<p className="stream-notice">{notice}</p>}
    <ModelSettings key={session.id} session={session} api={api} newId={newId} onApplied={onApplied}/>
    <div className="session-sheet-actions"><button onClick={refresh}>刷新会话</button>{['remote','managed'].includes(session.kind)&&<button className="stop-action" disabled={!canStop} onClick={stop}>停止当前执行</button>}</div>
    <div className="session-sheet-links"><button onClick={()=>navigate('operations')}>操作记录</button><button onClick={()=>navigate('connection')}>连接状态</button></div>
  </section></div>;
}
