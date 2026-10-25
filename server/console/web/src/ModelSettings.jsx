import React, {useEffect, useRef, useState} from 'react';

const modelNames={opus:'Opus',sonnet:'Sonnet',haiku:'Haiku',fable:'Fable',default:'会话默认模型'};
const reasons={
  model_settings_changed:'原会话设置已变化，请刷新后重新选择。',
  model_settings_pending:'上次切换仍在确认，请等待回执。',
  model_settings_ultracode_unknown:'请明确选择 Ultracode 开启或关闭，再调整 effort。',
  model_settings_remote_only:'此入口目前支持 Remote Control 会话。',
  model_settings_official_verification:'原会话要求官方设备验证，请在官方页面完成。',
  model_settings_effort_override:'原会话的环境设置固定了 effort，未能切换。',
  model_settings_ultracode_unavailable:'原会话当前不支持所选 Ultracode 设置。',
  model_settings_unsupported:'原会话当前版本不支持这项切换。',
  model_settings_unavailable:'原会话暂时无法使用这项设置，请刷新后检查。',
  model_settings_rejected:'原会话拒绝了这次切换，当前值保持以回报为准。',
  model_settings_outcome_unknown:'暂未取得完整回执，正在核对原请求，不会重复提交。',
};
const pending=row=>['requested','accepted','unconfirmed'].includes(row?.state);
const alias=model=>Object.keys(modelNames).find(k=>model===k||model?.startsWith('claude-'+k+'-'))||'';

export default function ModelSettings({session,api,newId,onApplied}){
  const [snapshot,setSnapshot]=useState(null),[model,setModel]=useState(''),[effort,setEffort]=useState(''),[ultra,setUltra]=useState('preserve');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[localPending,setLocalPending]=useState(false);
  const lock=useRef(false),initialized=useRef(false),seen=useRef(''),applied=useRef(onApplied);applied.current=onApplied;
  const pull=useRef(null);
  useEffect(()=>{
    if(session.kind!=='remote')return;
    let stopped=false,timer,controller;
    async function read(){
      clearTimeout(timer);controller?.abort();controller=new AbortController();
      try{
        const result=await api('/api/model-settings/'+session.id,undefined,{signal:controller.signal});
        if(stopped)return;
        setSnapshot(result);
        if(!initialized.current){setModel(alias(result.current.model)||result.models[0]);setEffort(result.current.effort||'high');initialized.current=true;}
        if(result.operation){setLocalPending(false);if(['applied','rejected'].includes(result.operation.state)&&seen.current!==result.operation.operation_id){seen.current=result.operation.operation_id;applied.current?.();}}
      }catch(e){if(!stopped&&e.name!=='AbortError')setError(reasons[e.code]||e.message);}
      finally{if(!stopped)timer=setTimeout(read,4000);}
    }
    pull.current=read;read();
    return()=>{stopped=true;clearTimeout(timer);controller?.abort();pull.current=null;};
  },[session.id,session.kind,api]);
  if(session.kind!=='remote')return <p className="settings-hint">此处可切换 Remote Control 原会话；独立后台会话可在新建时选择模型和 effort。</p>;
  const actual=snapshot?.current,op=snapshot?.operation,waiting=busy||localPending||pending(op),disabled=waiting||!actual?.connected;
  async function submit(setting){
    if(lock.current||disabled)return;
    lock.current=true;setBusy(true);setError('');
    const expected={model:actual.model,effort:actual.effort,ultracode:actual.ultracode};
    try{
      const result=await api('/api/action',{action:'model_settings',session_id:session.id,operation_id:newId(),setting,value:setting==='model'?model:effort,expected,...(setting==='effort'?{ultracode:ultra}:{})});
      setSnapshot(old=>({...old,operation:result.operation}));pull.current?.();
    }catch(e){
      setError(reasons[e.code]||e.message);
      if(!e.httpStatus||['operation_pending_or_outcome_unknown','connection_timeout_outcome_unknown'].includes(e.code))setLocalPending(true);
      pull.current?.();
    }finally{lock.current=false;setBusy(false);}
  }
  return <section className="model-settings" aria-label="模型与 effort 设置">
    <h3>模型与 effort</h3>
    {!actual?<p role="status">正在读取原会话设置…</p>:<>
      <p className="settings-current">当前：<strong>{actual.model.replace('claude-','')||'未回报'}</strong> · effort <strong>{actual.effort||'未回报'}</strong> · Ultracode {actual.ultracode===null?'未回报':actual.ultracode?'开启':'关闭'}</p>
      <div className="settings-row"><label>模型<select aria-label="切换模型" value={model} onChange={e=>setModel(e.target.value)} disabled={disabled}>{snapshot.models.map(m=><option key={m} value={m}>{modelNames[m]||m}</option>)}</select></label><button disabled={disabled||model===alias(actual.model)} onClick={()=>submit('model')}>应用模型</button></div>
      <p className="settings-hint">可用性以原会话确认为准。切换只用于这个会话后续的模型请求。</p>
      <p className="settings-hint">Ultracode 是独立的工作流开关，可与不同 effort 搭配。</p><div className="settings-row"><label>Effort<select aria-label="切换 effort" value={effort} onChange={e=>setEffort(e.target.value)} disabled={disabled}>{snapshot.efforts.map(e=><option key={e}>{e}</option>)}</select></label><label>Ultracode<select aria-label="Ultracode 设置" value={ultra} onChange={e=>setUltra(e.target.value)} disabled={disabled}><option value="preserve" disabled={actual.ultracode===null}>保持当前</option><option value="on">开启</option><option value="off">关闭</option></select></label></div>
      {actual.ultracode===null&&ultra==='preserve'&&<p className="settings-hint">原会话未回报 Ultracode 状态。调整 effort 前，请选择开启或关闭，避免隐式改变这个独立开关。</p>}
      <button className="apply-effort" disabled={disabled||(ultra==='preserve'&&actual.ultracode===null)||(effort===actual.effort&&(ultra==='preserve'||(ultra==='on')===actual.ultracode))} onClick={()=>submit('effort')}>应用 effort 设置</button>
      {!actual.connected&&<p className="settings-hint">会话重新连接后即可切换。</p>}
    </>}
    {(op||localPending)&&<p className={'settings-result '+(op?.state||'unconfirmed')} role="status">{localPending?'提交结果待核对，正在读取回执。':op.state==='applied'?`已生效：${op.actual?.model?.replace('claude-','')||''} · effort ${op.actual?.effort||'未回报'}。`:op.state==='rejected'?(reasons[op.error]||'原会话拒绝了这次切换。'):op.state==='unconfirmed'?reasons.model_settings_outcome_unknown:'已请求切换，等待原会话回执…'}</p>}
    {error&&<p className="settings-error" role="alert">{error}</p>}
    <button className="settings-refresh" onClick={()=>{setError('');pull.current?.();}}>重新读取设置</button>
  </section>;
}
