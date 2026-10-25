import React, {memo, useCallback, useEffect, useRef, useState} from 'react';
import katex from 'katex';
import './artifacts.css';
import {artifactHtml} from './artifactExport.js';

const messages = {
  artifact_local_history_unavailable: '正在核对 Remote Control 记录，请稍后刷新。',
  artifact_replay_incomplete: '记录中的文档更新尚不能完整还原，已保留原成品链接。',
  artifact_not_in_session: '在这个会话的可读记录中没有找到此成品。',
  artifact_session_unavailable: '会话暂时无法读取，请稍后刷新。',
  artifact_read_unavailable: '成品读取暂时不可用，请稍后刷新。',
  artifact_snapshot_unavailable: '记录中还没有完整的文档快照。可先打开原成品；后续有完整读取记录时会自动识别。',
};
const when = value => value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '时间未提供';

const MathBlock = memo(function MathBlock({text}) {
  let html;
  try {html = katex.renderToString(text, {displayMode:true, output:'mathml', trust:false, throwOnError:false, strict:'ignore', maxExpand:1000, maxSize:20});}
  catch {return <pre>{text}</pre>;}
  return <div className="artifact-math" dangerouslySetInnerHTML={{__html:html}}/>;
});

function DocumentNode({node, zoom}) {
  if (node.tag === 'text') return node.text;
  if (node.tag === 'math') return <MathBlock text={node.text}/>;
  if (node.tag === 'pre') return <pre><code>{node.text}</code></pre>;
  if (node.tag === 'placeholder') return <p className="stream-notice">此段尚未完成：{node.text}</p>;
  if (node.tag === 'figure') return <figure>
    {node.image ? <button className="artifact-figure-button" aria-label={'放大插图：'+node.caption} onClick={()=>zoom({caption:node.caption,...node.image})}><img src={node.image.src} alt={node.caption} loading="lazy"/></button> : <p className="stream-notice">这张插图尚无可用快照。</p>}
    <figcaption>{node.caption}{node.image?.render_warning&&<span> · 原图记录含布局提示</span>}{node.image?.stale && <span> · 此图捕获后有修订，原页可查看最新版</span>}<span className="artifact-figure-hint"> · 点击放大</span></figcaption>
  </figure>;
  const children = node.children?.map((child,i)=><DocumentNode key={i} node={child} zoom={zoom}/>);
  if (node.tag === 'table') return <div className="artifact-table"><table><tbody>{children}</tbody></table></div>;
  if (node.tag === 'a') return <a href={node.href} target="_blank" rel="noreferrer">{children}</a>;
  if (node.tag === 'br') return <br/>;
  const tag = /^(article|p|span|strong|em|s|u|code|ul|ol|li|tr|th|td|blockquote|h[1-6])$/.test(node.tag) ? node.tag : 'span';
  return React.createElement(tag, node.id ? {id:node.id} : null, children);
}

function useArtifactRead(path, api, revision) {
  const [state,setState] = useState({path:null,value:null,error:'',loading:true});
  useEffect(()=>{
    if (!path) return;
    let alive=true, timer;
    const controller=new AbortController();
    setState(old=>old.path===path?{...old,loading:true,error:''}:{path,value:null,error:'',loading:true});
    async function poll(){
      try {
        const result=await api(path,undefined,{signal:controller.signal});
        if(!alive)return;
        const error=result.sync?.error || '';
        setState(old=>({path,value:result.loading?old.value:result,error,loading:result.loading||result.sync?.refreshing}));
        if(!error&&(result.loading||result.sync?.refreshing||result.refresh?.state==='running')) timer=setTimeout(poll,result.refresh?.state==='running'?1500:750);
      }catch(error){if(alive)setState(old=>({...old,path,error:error.message,loading:false}));}
    }
    poll();return()=>{alive=false;controller.abort();clearTimeout(timer);};
  },[path,api,revision]);
  return state.path===path?state:{value:null,error:'',loading:!!path};
}

export default function ArtifactPane({session, initialId, api, close}) {
  const [selected,setSelected]=useState(initialId||null),[revision,setRevision]=useState(0),[zoom,setZoom]=useState(null);
  const panel=useRef(null),documentRoot=useRef(null),zoomSource=useRef(null),completedRefresh=useRef(null);
  const [starting,setStarting]=useState(false),[refreshError,setRefreshError]=useState('');
  const openZoom=useCallback(value=>{zoomSource.current=document.activeElement;setZoom(value);},[]);
  function closeZoom(){setZoom(null);requestAnimationFrame(()=>zoomSource.current?.focus?.());}
  const list=useArtifactRead('/api/artifacts/'+session,api,revision);
  const exports=useArtifactRead('/api/artifact-exports',api,revision);
  const bundle=exports.value?.export;
  const refresh=exports.value?.refresh;
  const refreshing=starting||refresh?.state==='running';
  useEffect(()=>{
    if(refresh?.state==='complete'&&refresh.id!==completedRefresh.current){completedRefresh.current=refresh.id;setRevision(x=>x+1);}
  },[refresh?.id,refresh?.state]);
  async function refreshAll(){
    setStarting(true);setRefreshError('');
    try{await api('/api/artifact-exports/refresh',{});}
    catch{setRefreshError('更新请求暂未确认，请查看后台状态后再试。');}
    finally{setStarting(false);setRevision(x=>x+1);}
  }
  const read=useArtifactRead(selected?'/api/artifacts/'+session+'/'+selected:null,api,revision);
  const artifact=read.value?.artifact;
  useEffect(()=>{
    const previous=document.activeElement;panel.current?.focus();
    return()=>previous?.focus?.();
  },[]);
  function keys(event){
    if(event.key==='Escape'){event.stopPropagation();zoom?closeZoom():close();}
    if(event.key==='Tab'){
      const scope=zoom?panel.current.querySelector('.artifact-zoom'):panel.current;
      const elements=[...scope.querySelectorAll('button,a[href],summary,[tabindex="0"]')].filter(e=>!e.disabled&&e.getClientRects().length);
      const first=elements[0],last=elements.at(-1);
      if(event.shiftKey&&(document.activeElement===first||document.activeElement===panel.current)){event.preventDefault();last?.focus();}
      if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    }
  }
  function download(){
    if(!documentRoot.current||!artifact?.available)return;
    const html=artifactHtml(artifact);
    const url=URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download=(artifact.title||'artifact').replace(/[\\/:*?"<>|]/g,'-').slice(0,100)+'.html';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  return <div className="artifact-scrim" onClick={close}>
    <section className={'artifact-panel '+(selected?'artifact-selected':'')} ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="会话成品" onClick={e=>e.stopPropagation()} onKeyDown={keys}>
      <header className="artifact-header"><div><h2>会话成品</h2><p>文档与图表 · 只读快照</p></div><div className="artifact-actions"><button onClick={()=>setRevision(x=>x+1)}>刷新成品</button><button aria-label="关闭成品" onClick={close}>关闭</button></div></header>
      <div className="artifact-batch"><div><strong>全部 Remote Control 会话</strong>{bundle?<><span>{bundle.session_count} 个会话 · {bundle.html_count} 份 HTML · {bundle.link_only_count} 项仅有链接</span><small>导出于 {when(bundle.created_at)}</small></>:<span>更新快照后即可批量下载</span>}</div><div className="artifact-batch-controls"><button onClick={refreshAll} disabled={refreshing}>{refreshing?'正在更新…':'更新全部快照'}</button>{bundle&&<a href={'/api/artifact-exports/'+bundle.id+'/download'}>下载全部 HTML（ZIP）</a>}</div></div>
      {(refresh?.state&&refresh.state!=='idle'||refreshError)&&<div className={'artifact-refresh-progress '+(['failed','interrupted'].includes(refresh?.state)?'failed':'')} role="status">{refreshError||refresh.message}{refresh?.state==='running'&&<span>{refresh.artifact_title||refresh.session_title||''}{refresh.artifacts_total!=null?' · '+(refresh.artifacts_done||0)+'/'+refresh.artifacts_total:''} · 上一版仍可下载</span>}{refresh?.state==='complete'&&<span>新增 {refresh.new||0} · 更新 {refresh.updated||0} · 复用 {refresh.reused||0}{refresh.retained?' · 保留旧副本 '+refresh.retained:''}</span>}</div>}
      <div className="artifact-layout"><aside className="artifact-nav" aria-label="成品列表">
        {list.loading&&!list.value&&<p className="muted">正在查找成品…</p>}
        {list.error&&<p className="stream-notice">{messages[list.error]||list.error}</p>}
        {[...(list.value?.artifacts||[])].reverse().map(item=><button className={selected===item.id?'selected':''} key={item.id} onClick={()=>{setSelected(item.id);setZoom(null);}}><strong>{item.title==='Untitled'?'未命名成品':item.title}</strong><small>查看快照</small></button>)}
        {!list.loading&&!list.error&&!list.value?.artifacts?.length&&<p className="muted">近期记录中还没有发现成品。</p>}
        <p className="artifact-scope-note">从本地及 Remote Control 记录识别，无需本地会话映射。快照时间不代表原页的最新版本。</p>
      </aside><div className="artifact-reader">
        {!selected?<div className="artifact-welcome"><h3>选择一个成品</h3><p>正文、表格、公式和已有插图可直接阅读。</p></div>:<>
          <div className="artifact-toolbar"><button className="artifact-back" onClick={()=>setSelected(null)}>← 成品列表</button>{artifact?.available&&<button onClick={download}>下载 HTML</button>}{artifact?.official_url&&<a href={artifact.official_url} target="_blank" rel="noreferrer">打开原成品 ↗</a>}</div>
          {read.error&&<p className="stream-notice">{messages[read.error]||read.error}</p>}
          {read.loading&&!artifact&&<p className="muted">正在读取文档和插图…</p>}
          {artifact&&!artifact.available&&<div className="artifact-welcome"><h3>{artifact.title}</h3><p>{messages[artifact.reason]||'此成品暂时只有原页链接。'}</p></div>}
          {artifact?.available&&<>
            <div className="artifact-version">版本 {artifact.revision} · 读取于 {when(artifact.captured_at)} · 插图 {artifact.available_figures}/{artifact.figure_count}{read.loading?' · 正在更新':''}</div>
            {artifact.snapshot_outdated&&<p className="stream-notice">当前为可核对的版本 {artifact.revision}；记录中还有版本 {artifact.latest_observed_revision} 的更新，未将无法还原的修订混入本文。</p>}
            {!!artifact.stale_figures&&<p className="stream-notice">{artifact.stale_figures} 张插图在捕获后有修订，当前显示已保存的图像快照。</p>}
            {!!artifact.missing?.length&&<p className="stream-notice">有 {artifact.missing.length} 项内容尚不能完整呈现，相关位置已保留提示。</p>}
            <details className="artifact-toc"><summary>文档目录</summary><nav>{artifact.toc.filter(x=>x.level<=3).map(x=><a key={x.id} href={'#'+x.id} onClick={e=>{e.preventDefault();documentRoot.current?.querySelector('#'+x.id)?.scrollIntoView({block:'start',behavior:'smooth'});}}>{x.title}</a>)}</nav></details>
            <div className="artifact-document" ref={documentRoot}><DocumentNode node={artifact.content} zoom={openZoom}/></div>
          </>}
        </>}
      </div></div>
      {zoom&&<div className="artifact-zoom" role="dialog" aria-label="插图原图"><header><strong>{zoom.caption}</strong><button autoFocus onClick={closeZoom}>关闭原图</button></header><div><img src={zoom.src} alt={zoom.caption}/></div></div>}
    </section>
  </div>;
}
