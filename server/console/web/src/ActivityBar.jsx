import React, {memo, useEffect, useState, useRef} from 'react';
import {duration, presentation} from './activity.js';
import './activity.css';

export function useSessionActivity(auth, session, api) {
  const [state, setState] = useState(null);
  const snapshots=useRef(new Map());
  const id = session?.id, remote = session?.kind === 'remote';
  useEffect(() => {
    if (!auth || !id || !remote) {if(!auth)snapshots.current.clear();setState(null); return;}
    let live = true, timer;
    const controller = new AbortController();
    setState(snapshots.current.get(id)||{session:id,value:null,error:'',receivedAt:Date.now()});
    async function poll() {
      let delay=2500;
      try {
        const data = await api('/api/activity/'+encodeURIComponent(id), undefined, {signal:controller.signal});
        if (!live) return;
        if (data.session_id !== id) throw Error('activity_session_mismatch');
        if (data.activity) {const next={session:id,value:data.activity,error:data.sync?.error || '',receivedAt:Date.now()-(data.sync?.age_seconds||0)*1000};snapshots.current.delete(id);snapshots.current.set(id,next);while(snapshots.current.size>8)snapshots.current.delete(snapshots.current.keys().next().value);setState(next);}
        else if (data.sync?.error) setState(old=>({...old,error:data.sync.error}));
        if (data.loading || data.sync?.restored) delay=750;
        else if (['idle','complete','stopped'].includes(data.activity?.phase)) delay=5000;
      } catch (error) {
        if(live) setState(old=>({...old,error:error.message}));
        delay=5000;
      } finally {if(live) timer=setTimeout(poll,delay);}
    }
    poll();
    return()=>{live=false;controller.abort();clearTimeout(timer);};
  },[auth,id,remote,api]);
  return state;
}

export const ActivityBar = memo(function ActivityBar({session, detail, state, delivery}) {
  const [now,setNow]=useState(Date.now);
  const [expanded,setExpanded]=useState(false);
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  const value=presentation(session,detail,state,now);
  const moving=!value.stale && ['thinking','working','writing','tool','preparing_tool','compacting','queued'].includes(value.phase);
  const recentDelivery=delivery && delivery.session===session.id;
  const attention=value.stale||value.phase==='error'||(recentDelivery&&(delivery.status!=='accepted'||now-delivery.startedAt-(delivery.elapsed||0)<15000));
  return <section className={'activity-bar phase-'+value.phase+(expanded?' is-expanded':'')+(attention?' needs-attention':'')} aria-label="会话处理状态">
    <div className="activity-main">
      <span className={'activity-symbol '+(moving?'is-moving':'')} aria-hidden="true">{['complete','idle'].includes(value.phase)?'✓':['error','disconnected'].includes(value.phase)?'!':'✳'}</span>
      <div className="activity-description"><strong role="status" aria-live="polite"><span className="activity-full-label">{value.label}</span><span className="activity-short-label">{value.label.split(' · ').at(-1)}</span></strong>{value.detail&&<span className="activity-detail">{value.detail}</span>}</div>
      {value.elapsed!==null&&<span className="activity-timer" title={value.timerLabel==='本轮'?'按原会话报告的时间计算':'原会话未提供完整起点，显示可确认的观察时长'}><small>{value.timerLabel}</small><time>{duration(value.elapsed)}</time></span>}
      <button className="activity-expand" aria-label={expanded?'收起状态详情':'展开状态详情'} aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}><span aria-hidden="true">{expanded?'⌃':'⌄'}</span></button>
    </div>
    <div className="activity-foot">
      <span>{value.stale?'正在恢复状态同步':state?.value?.source==='native_service_events'?'原会话状态 · 未回报细分思考阶段':state?.value?'状态已同步':'根据会话状态显示'}</span>
      {recentDelivery&&<span className={'delivery delivery-'+delivery.status} role="status">
        {delivery.status==='submitting'?`正在送达 · ${duration(now-delivery.startedAt)}`:
          delivery.status==='accepted'?`Claude 已接收 · 送达用时 ${duration(delivery.elapsed)}`:
          delivery.status==='rejected'?'未发送，请按提示处理后重试':
          '送达结果待核对，请先查看消息记录'}
      </span>}
    </div>
  </section>;
});
