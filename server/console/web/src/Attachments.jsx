import React,{useEffect,useRef,useState} from 'react';
import './attachments.css';

const CHUNK=256*1024;
const errors={attachment_file_limit:'文件超过 128 MiB',attachment_image_limit:'图片超过 5 MiB',attachment_storage_limit:'附件暂存空间已满',attachment_requires_local_project:'此会话在其他设备，当前客户端只能向它发送图片',attachment_image_invalid:'图片格式无效',attachment_invalid_name:'文件名不可用'};
const identifier=()=>[...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('');
const encoded=bytes=>{let value='';for(let i=0;i<bytes.length;i+=8192)value+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(value);};

export function useAttachments(session,api,authenticated=true){
 const [drafts,setDrafts]=useState({}),store=useRef(new Map());
 const update=(sid,id,patch)=>{const row=store.current.get(id);if(!row)return;Object.assign(row,patch);setDrafts(old=>({...old,[sid]:[...store.current.values()].filter(x=>x.session===sid)}));};
 useEffect(()=>()=>{for(const row of store.current.values()){row.controller.abort();if(row.preview)URL.revokeObjectURL(row.preview);}},[]);
 useEffect(()=>{if(authenticated!==false)return;for(const row of store.current.values()){row.controller.abort();if(row.preview)URL.revokeObjectURL(row.preview);}store.current.clear();setDrafts({});},[authenticated]);
 async function upload(row){
  const {id,session:sid,file}=row;update(sid,id,{state:'uploading',error:''});
  const call=data=>api('/api/attachments',{session_id:sid,id,...data},{signal:row.controller.signal});
  try{
   let value;for(let attempt=0;attempt<3;attempt++){try{value=await call({operation:'begin',name:file.name,size:file.size});break;}catch(e){if(row.controller.signal.aborted||attempt===2||e.httpStatus===409)throw e;}}
   let offset=value.attachment.offset,failures=0;
   while(offset<file.size){
    const bytes=new Uint8Array(await file.slice(offset,offset+CHUNK).arrayBuffer());
    try{value=await call({operation:'chunk',offset,data:encoded(bytes)});offset=value.attachment.offset;failures=0;}
    catch(e){if(row.controller.signal.aborted||++failures>3||e.httpStatus===409)throw e;value=await call({operation:'status'});offset=value.attachment.offset;}
    update(sid,id,{progress:file.size?Math.round(offset/file.size*100):100});
   }
   value=await call({operation:'finish'});update(sid,id,{state:'ready',progress:100});
  }catch(e){if(!row.controller.signal.aborted)update(sid,id,{state:'error',error:errors[e.code]||e.message});}
 }
 function add(files){
  const current=[...store.current.values()].filter(x=>x.session===session);
  if(current.length+files.length>8)throw Error('每条消息最多 8 个附件');
  for(const file of files){const row={id:identifier(),session,file,name:file.name,state:'uploading',progress:0,controller:new AbortController(),preview:/^image\/(png|jpeg|gif|webp)$/.test(file.type)?URL.createObjectURL(file):null};store.current.set(row.id,row);update(session,row.id,{});upload(row);}
 }
 function forget(row){row.controller.abort();if(row.preview)URL.revokeObjectURL(row.preview);store.current.delete(row.id);setDrafts(old=>({...old,[row.session]:(old[row.session]||[]).filter(x=>x.id!==row.id)}));}
 function remove(row){forget(row);api('/api/attachments',{operation:'remove',session_id:row.session,id:row.id}).catch(()=>{});}
 function clear(sid){for(const row of [...store.current.values()])if(row.session===sid)forget(row);}
 const rows=drafts[session]||[];
 return {rows,add,remove,clear,ids:rows.filter(x=>x.state==='ready').map(x=>x.id),pending:rows.some(x=>x.state!=='ready'),retry:row=>{row.controller=new AbortController();upload(row);}};
}

export function AttachmentPicker({attachments,disabled,onError}){
 const input=useRef(null);
 return <div className="attachment-picker"><input ref={input} type="file" multiple aria-label="选择图片或文件" hidden onChange={e=>{try{attachments.add([...e.target.files]);}catch(error){onError(error.message);}e.target.value='';}}/>
 <button type="button" className="attachment-add" disabled={disabled} aria-label="添加图片或文件" title="添加图片或文件" onClick={()=>input.current.click()}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m8 12 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9M6 14l8-8"/></svg><span>附件</span></button>
 <div className="attachment-list">{attachments.rows.map(row=><article className={'attachment-item '+row.state} key={row.id}>
 {row.preview?<img src={row.preview} alt="附件预览"/>:<span className="attachment-file" aria-hidden="true">↗</span>}
 <div><strong title={row.name}>{row.name}</strong><small>{row.state==='ready'?'已就绪':row.state==='error'?row.error:`上传 ${row.progress}%`}</small>{row.state==='uploading'&&<progress max="100" value={row.progress} aria-label={row.name+' 上传进度'}/>}</div>
 {row.state==='error'&&<button type="button" disabled={disabled} onClick={()=>attachments.retry(row)}>重试</button>}
 <button type="button" disabled={disabled} aria-label={'移除 '+row.name} onClick={()=>attachments.remove(row)}>×</button></article>)}</div></div>;
}
