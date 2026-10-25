import React,{useEffect,useRef,useState} from 'react';
import {getDocument,GlobalWorkerOptions} from 'pdfjs-dist/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url';
import './pdf.css';

GlobalWorkerOptions.workerSrc=workerUrl;

export default function PdfPreview({item,url,close}) {
  const panel=useRef(null),viewport=useRef(null),canvas=useRef(null),activeRender=useRef(null);
  const [pdf,setPdf]=useState(null),[page,setPage]=useState(1),[zoom,setZoom]=useState(1),[width,setWidth]=useState(0);
  const [loading,setLoading]=useState(true),[rendering,setRendering]=useState(false),[error,setError]=useState(''),[pageText,setPageText]=useState('');
  useEffect(()=>{
    let alive=true;
    const before=document.activeElement;panel.current?.focus();
    const task=getDocument({url,withCredentials:true,rangeChunkSize:1048576,disableAutoFetch:true,disableStream:true,
      isEvalSupported:false,enableXfa:false,useWasm:false,cMapPacked:true,
      cMapUrl:'/pdf-assets/6.4.299/cmaps/',standardFontDataUrl:'/pdf-assets/6.4.299/standard_fonts/'});
    task.onPassword=()=>{if(alive){setError('这份 PDF 设置了密码，请下载后在 PDF 阅读器中打开。');setLoading(false);}task.destroy();};
    task.promise.then(doc=>{if(alive){setPdf(doc);setLoading(false);}}).catch(()=>{if(alive){setError(old=>old||'PDF 暂时无法预览，请关闭后重试，或下载原文件。');setLoading(false);}});
    return()=>{alive=false;task.destroy();before?.focus?.();};
  },[url]);
  useEffect(()=>{
    const element=viewport.current;if(!element)return;
    const observer=new ResizeObserver(entries=>setWidth(Math.round(entries[0].contentRect.width)));
    observer.observe(element);return()=>observer.disconnect();
  },[]);
  useEffect(()=>{
    if(!pdf||!width)return;
    let alive=true,renderTask;
    setRendering(true);setPageText('');
    (async()=>{
      const sheet=await pdf.getPage(page);
      if(!alive)return;
      if(activeRender.current){activeRender.current.cancel();await activeRender.current.promise.catch(()=>{});}
      if(!alive)return;
      const base=sheet.getViewport({scale:1});
      const view=sheet.getViewport({scale:Math.min((Math.max(width,200)-28)/base.width,1.65)*zoom});
      const ratio=Math.min(window.devicePixelRatio||1,2,Math.sqrt(8000000/(view.width*view.height)));
      const target=canvas.current;
      target.width=Math.floor(view.width*ratio);target.height=Math.floor(view.height*ratio);
      target.style.width=view.width+'px';target.style.height=view.height+'px';
      renderTask=sheet.render({canvasContext:target.getContext('2d'),viewport:view,transform:ratio===1?null:[ratio,0,0,ratio,0,0]});
      activeRender.current=renderTask;
      await renderTask.promise;
      if(alive){setRendering(false);setError('');}
      const text=await sheet.getTextContent();
      if(alive)setPageText(text.items.map(x=>x.str||'').join(' '));
    })().catch(error=>{if(alive&&error.name!=='RenderingCancelledException'){setRendering(false);setError('这一页暂时无法显示，可以重试翻页或下载原文件。');}});
    return()=>{alive=false;renderTask?.cancel();};
  },[pdf,page,zoom,width]);
  function keys(event){
    if(event.key==='Escape'){event.preventDefault();close();}
    if(event.key==='Tab'){
      const controls=[...panel.current.querySelectorAll('button,a[href],input')].filter(e=>!e.disabled&&e.getClientRects().length);
      const first=controls[0],last=controls.at(-1);
      if(event.shiftKey&&(document.activeElement===first||document.activeElement===panel.current)){event.preventDefault();last?.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    }
  }
  return <div className="pdf-scrim" onClick={close}>
    <section ref={panel} tabIndex={-1} className="pdf-panel" role="dialog" aria-modal="true" aria-label={'PDF 预览：'+item.name} onClick={e=>e.stopPropagation()} onKeyDown={keys}>
      <header className="pdf-header"><div><span>PDF 文档</span><h2>{item.name}</h2></div><button aria-label="关闭 PDF 预览" onClick={close}>关闭</button></header>
      <nav className="pdf-controls" aria-label="PDF 翻页和缩放">
        <button disabled={!pdf||page<=1||rendering} onClick={()=>setPage(x=>x-1)}>上一页</button>
        <span aria-live="polite">{pdf?`第 ${page} / ${pdf.numPages} 页`:'正在读取…'}</span>
        <button disabled={!pdf||page>=pdf.numPages||rendering} onClick={()=>setPage(x=>x+1)}>下一页</button>
        <div className="pdf-zoom"><button aria-label="缩小 PDF" disabled={zoom<=.5} onClick={()=>setZoom(x=>Math.max(.5,x-.25))}>−</button><button onClick={()=>setZoom(1)}>{Math.round(zoom*100)}%</button><button aria-label="放大 PDF" disabled={zoom>=3} onClick={()=>setZoom(x=>Math.min(3,x+.25))}>＋</button></div>
        <a href={url+'?download=1'} download={item.name}>下载 PDF</a>
      </nav>
      {error&&<p className="pdf-error" role="alert">{error}</p>}
      <div className="pdf-viewport" ref={viewport}>
        {(loading||rendering)&&<p className="pdf-loading" role="status">{loading?'正在读取 PDF…':'正在绘制页面…'}</p>}
        <canvas ref={canvas} aria-label={`PDF 第 ${page} 页`} role="img" hidden={!pdf}/>
        <span className="pdf-sr-only">{pageText}</span>
      </div>
    </section>
  </div>;
}
