import React,{memo,useMemo} from 'react';
import {Streamdown} from 'streamdown';
import {code} from '@streamdown/code';
import {registeredReference} from './documentFiles.js';
import {mediaUrl} from './markdownLinks.js';
const plugins={code},noRawHtml=[];

export default memo(function RichMarkdown({text, streaming=false, attachments=[], session, onArtifact, onPdf}) {
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

