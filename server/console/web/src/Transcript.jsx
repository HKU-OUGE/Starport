import React, {memo, useMemo, useState, useCallback, lazy, Suspense} from 'react';
import {Streamdown} from 'streamdown';
import {code} from '@streamdown/code';
import 'streamdown/styles.css';
import {attachDocuments,documentReadKey,registeredReference} from './documentFiles.js';
import {useDocumentFiles} from './useDocumentFiles.js';
const PdfPreview=lazy(()=>import('./PdfPreview.jsx'));

const plugins = {code};
const noRawHtml = [];
const time = value => value ? new Date(value).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) : '';
const size = value => value < 1048576 ? `${(value / 1024).toFixed(0)} KiB` : `${(value / 1048576).toFixed(1)} MiB`;
export const mediaUrl = (session, item) => `/api/${item.source==='document'?'documents':'media'}/${encodeURIComponent(session)}/${item.id}`;
function eventKey(event) {
  let hash=2166136261;
  const content=(event.text||'')+(event.attachments||[]).map(x=>x.id||x.name).join('|');
  for(let i=0;i<content.length;i++)hash=Math.imul(hash^content.charCodeAt(i),16777619);
  return `${event.seq}:${event.role}:${event.tool_id||''}:${hash>>>0}`;
}

function MediaCard({item, session, onPdf}) {
  const [failed, setFailed] = useState(false);
  const url = item.available ? mediaUrl(session, item) : null;
  return <figure className="media-card">
    <figcaption><strong>{item.name}</strong><span>{item.size !== undefined ? size(item.size) : ''}</span></figcaption>
    {!url || failed ? <p className="media-error">{item.pending?'正在准备 PDF 附件…':item.error==='document_not_on_device'?'文件位于其他设备，当前连接无法读取原文件。':failed==='video'?'浏览器无法播放此视频，可以尝试下载后查看。':'附件暂时无法读取。文件可能已移动、变更，或不在此会话的可读范围。'}</p> :
      item.mime==='application/pdf' ? <div className="pdf-card-body"><span className="pdf-file-symbol">PDF</span><p>直接阅读文档，支持翻页与缩放。</p><button onClick={()=>onPdf?.(item)}>预览 PDF</button></div> :
      item.mime.startsWith('video/') ? <video src={url} controls playsInline preload="none" onError={()=>setFailed('video')} aria-label={item.name}/> :
        <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={item.name} loading="lazy" onError={()=>setFailed(true)}/></a>}
    {url && <div className="media-actions"><a href={url} target="_blank" rel="noreferrer">打开原文件</a><a href={url+'?download=1'} download={item.name}>下载</a></div>}
  </figure>;
}

function MediaList({items, session, onPdf}) {
  return items?.length ? <div className="media-list">{items.map((item,index)=><MediaCard key={item.id || item.name+'-'+index} item={item} session={session} onPdf={onPdf}/>)}</div> : null;
}

export const Markdown = memo(function Markdown({text, streaming=false, attachments=[], session, onArtifact, onPdf}) {
  const components = useMemo(()=>({
    strong: ({children}) => <strong>{children}</strong>,
    a: ({href, children}) => !href?<span>{children}</span>:<a href={href} target="_blank" rel="noreferrer" className={onArtifact&&/^https:\/\/claude\.ai\/(?:code\/)?artifact\/[A-Za-z0-9_-]{12,80}(?:[?#].*)?$/.test(href||'')?'artifact-link':undefined} onClick={e=>{if(e.metaKey||e.ctrlKey)return;const pdf=attachments.find(a=>a.available&&a.mime==='application/pdf'&&mediaUrl(session,a)===href);if(pdf&&onPdf){e.preventDefault();onPdf(pdf);}else if(onArtifact&&/^https:\/\/claude\.ai\/(?:code\/)?artifact\/[A-Za-z0-9_-]{12,80}(?:[?#].*)?$/.test(href||'')){e.preventDefault();onArtifact(href);}}}>{children}</a>,
    img: ({src, alt}) => <span className="inline-media-reference">{src?.startsWith('/api/media/') ? <a href={src} target="_blank" rel="noreferrer">{alt || '查看图片'}</a> : /^https?:\/\//.test(src||'') ? <a href={src} target="_blank" rel="noreferrer">{alt || '外部图片'}</a> : (alt || '图片见附件') }</span>,
    table: ({children}) => <div className="table-scroll"><table>{children}</table></div>,
  }), [onArtifact,onPdf,attachments,session]);
  const urls = new Map(attachments.filter(a=>a.available && a.reference).map(a=>[a.reference, mediaUrl(session,a)]));
  const urlTransform = value => registeredReference(urls,value) || (/^(https?:\/\/|mailto:|#)/i.test(value) ? value : '');
  return <Streamdown className="markdown" mode={streaming?'streaming':'static'} isAnimating={streaming}
    plugins={plugins} components={components} skipHtml rehypePlugins={noRawHtml} urlTransform={urlTransform}
    controls={false} shikiTheme={['github-light','github-light']} lineNumbers={false}>{text || ''}</Streamdown>;
});

function ToolCard({event, results=[], session, onPdf}) {
  const [open,setOpen] = useState(false);
  const input = event.input || {};
  const tool = event.tool_name || event.text || '工具';
  const command = typeof input.command === 'string' ? input.command : null;
  const brief = input.description || input.caption || input.file_path || input.path || input.pattern || '';
  const description = typeof brief === 'string' ? brief : JSON.stringify(brief);
  const error = results.some(x=>x.is_error || (x.exit_code !== null && x.exit_code !== undefined && x.exit_code !== 0));
  const label = results.length ? (error ? '失败' : '已返回') : '等待结果';
  const output = results.map(x=>x.text).filter(Boolean).join('\n');
  const fence = '`'.repeat(Math.max(3, ...((command||'').match(/`+/g)||[]).map(x=>x.length+1)));
  return <article className={'tool-card '+(error?'tool-error':'')}>
    <button className="tool-toggle" aria-expanded={open} onClick={()=>setOpen(!open)}>
      <span className="tool-chevron">{open?'▾':'▸'}</span><span className="tool-symbol">{tool==='Bash'?'$':'◇'}</span>
      <strong>{tool}</strong><span className="tool-summary">{description || command?.split('\n')[0] || '查看输入与结果'}</span><span className="tool-state">{label}</span>
    </button>
    {command && !open && <div className="command-preview">{command.split('\n').slice(0,2).join('\n')}</div>}
    {open && <div className="tool-body">
      <div className="tool-section-label">{command ? '执行命令' : '输入参数'}</div>
      {command ? <div className="command-highlight"><Markdown text={`${fence}bash\n${command}\n${fence}`}/></div> : <pre className="command-content">{event.input === undefined ? '这条旧记录没有保存输入参数。' : JSON.stringify(input,null,2)}</pre>}
      {event.input_truncated && <p className="truncation">输入过长，显示近期可用片段。</p>}
      {command && Object.keys(input).some(k=>!['command','description'].includes(k)) && <details className="tool-options"><summary>其他参数</summary><pre>{JSON.stringify(Object.fromEntries(Object.entries(input).filter(([k])=>!['command','description'].includes(k))),null,2)}</pre></details>}
      <div className="tool-section-label">输出 / 结果 {results.find(x=>x.exit_code!==null&&x.exit_code!==undefined)?.exit_code !== undefined ? ` · 退出码 ${results.find(x=>x.exit_code!==null&&x.exit_code!==undefined).exit_code}` : ''}</div>
      <pre className="tool-output">{output || (results.length ? '工具已返回，没有文本输出。' : '尚未收到结果；原始接口通常在命令结束后返回完整输出。')}</pre>
      {results.some(x=>x.truncated) && <p className="truncation">输出已截断，保留最多 64,000 个字符。</p>}
      <MediaList items={results.flatMap(x=>x.attachments||[])} session={session} onPdf={onPdf}/>
      <time>{time(event.time)}</time>
    </div>}
    <MediaList items={event.attachments} session={session} onPdf={onPdf}/>
  </article>;
}

export const Message = memo(function Message({event, session, streaming=false, onArtifact, onPdf}) {
  if(event.role==='permission')return null;
  if(event.role==='result')return <div className="turn-end">{event.text==='Turn failed'?'本轮执行失败':'本轮执行已结束'}</div>;
  if(event.role==='tool_result')return <article className="tool-card"><details className="orphan-result"><summary>工具结果{event.is_error?' · 失败':''} · 调用不在近期记录中</summary><pre className="tool-output">{event.text}</pre><MediaList items={event.attachments} session={session} onPdf={onPdf}/></details></article>;
  return <article className={`message ${event.role}`}>
    <div className="message-label">{event.role==='user'?'你':event.role==='assistant'?'Claude':'状态'}</div>
    {event.text && <div className="bubble"><Markdown text={event.text} streaming={streaming} attachments={event.attachments} session={session} onArtifact={onArtifact} onPdf={onPdf}/></div>}
    <MediaList items={event.attachments} session={session} onPdf={onPdf}/>
    {event.truncated && <p className="truncation">此消息过长，显示内容已截断。</p>}
    {event.time&&<time>{time(event.time)}</time>}
  </article>;
});

export const Transcript = memo(function Transcript({events, session, onArtifact, api}) {
  const [pdf,setPdf]=useState(null);
  const openPdf=useCallback(item=>setPdf(item),[]);
  const readKey=useMemo(()=>documentReadKey(events),[events]);
  const documents=useDocumentFiles(session,readKey,api);
  const enriched=useMemo(()=>attachDocuments(events,documents.files,documents.loading,documents.error),[events,documents.files,documents.loading,documents.error]);
  const {results, ids} = useMemo(()=>{
    const results=new Map(), ids=new Set();
    for(const e of events){
      if(e.role==='tool' && e.tool_id)ids.add(e.tool_id);
      if(e.role==='tool_result' && e.tool_id)results.set(e.tool_id,[...(results.get(e.tool_id)||[]),e]);
    }
    return {results,ids};
  },[events]);
  return <>{enriched.map((e,i)=> e.role==='tool_result'&&ids.has(e.tool_id) ? null : e.role==='tool' ?
    <ToolCard key={'tool-'+(e.tool_id||e.seq+'-'+i)} event={e} results={results.get(e.tool_id)} session={session} onPdf={openPdf}/> :
    <Message key={eventKey(e)} event={e} session={session} onArtifact={onArtifact} onPdf={openPdf}/>)}
    {pdf&&<Suspense fallback={<div className="pdf-loading">正在加载 PDF 阅读器…</div>}><PdfPreview key={session+pdf.id} item={pdf} url={mediaUrl(session,pdf)} close={()=>setPdf(null)}/></Suspense>}</>;
});
