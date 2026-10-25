import katex from 'katex';

export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeHref = value => /^(https?:\/\/|mailto:|#)/i.test(value || '') ? escapeHtml(value) : '';
export const exportStyle = `body{font:16px/1.85 system-ui,sans-serif;color:#243444;max-width:960px;margin:40px auto;padding:0 24px}h1,h2,h3{line-height:1.4}h2,h3{margin-top:2em}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #dbe2e8;padding:9px;text-align:left}th{background:#f1f5f7}img{max-width:100%;height:auto}figure{margin:24px 0}figcaption,.snapshot-note{color:#677581;font-size:13px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:14px}code{font-size:.9em}.artifact-math{overflow:auto;padding:16px}a{color:#11784e}.artifact-table{overflow:auto}math{max-width:100%}.notice{background:#fff5df;padding:12px 16px;border-radius:6px}.directory{padding:16px 24px;background:#f1f6f4;border-radius:8px}@media(max-width:600px){body{margin:20px auto;padding:0 16px}h1{font-size:26px}table{min-width:500px}}@media print{body{margin:0;max-width:none}table,figure{break-inside:avoid}}`;

export function exportNode(node) {
  const text = escapeHtml(node.text);
  if (node.tag === 'text') return text;
  if (node.tag === 'pre') return '<pre><code>'+text+'</code></pre>';
  if (node.tag === 'math') {
    try { return '<div class="artifact-math">'+katex.renderToString(node.text, {displayMode:true,output:'mathml',trust:false,throwOnError:false,strict:'ignore',maxExpand:1000,maxSize:20})+'</div>'; }
    catch { return '<pre>'+text+'</pre>'; }
  }
  if (node.tag === 'placeholder') return '<p class="notice">此段尚未完成：'+text+'</p>';
  if (node.tag === 'figure') {
    const image = node.image && /^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(node.image.src) ? '<img src="'+node.image.src+'" alt="'+escapeHtml(node.caption)+'">' : '<p class="notice">这张插图尚无可用快照。</p>';
    return '<figure>'+image+'<figcaption>'+escapeHtml(node.caption)+(node.image?.stale?' · 此图捕获后有修订，原页可查看最新版':'')+(node.image?.render_warning?' · 原图记录含布局提示':'')+'</figcaption></figure>';
  }
  const children=(node.children || []).map(exportNode).join('');
  if(node.tag==='table') return '<div class="artifact-table"><table><tbody>'+children+'</tbody></table></div>';
  if(node.tag==='a') return '<a href="'+safeHref(node.href)+'" rel="noreferrer">'+children+'</a>';
  if(node.tag==='br') return '<br>';
  const tag=/^(article|p|span|strong|em|s|u|code|ul|ol|li|tr|th|td|blockquote|h[1-6])$/.test(node.tag)?node.tag:'span';
  return '<'+tag+(node.id?' id="'+escapeHtml(node.id)+'"':'')+'>'+children+'</'+tag+'>';
}

export function htmlPage(title, body) {
  return '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; img-src data:; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;"><meta name="referrer" content="no-referrer"><title>'+escapeHtml(title)+'</title><style>'+exportStyle+'</style></head><body>'+body+'</body></html>';
}

export function artifactHtml(artifact) {
  if(!artifact.available || !artifact.content) throw new Error('artifact_snapshot_unavailable');
  const toc=(artifact.toc || []).filter(x=>x.level<=3).map(x=>'<li><a href="#'+escapeHtml(x.id)+'">'+escapeHtml(x.title)+'</a></li>').join('');
  const warnings=[artifact.stale_figures ? artifact.stale_figures+' 张插图有后续修订，本文件保存的是已捕获快照。' : '', artifact.missing?.length ? artifact.missing.length+' 项内容未能完整导出，相关位置保留提示。' : '', artifact.snapshot_outdated ? '记录中还有更新版本；本文件导出最近一次完整读取的正文。' : ''].filter(Boolean);
  return htmlPage(artifact.title, '<header><h1>'+escapeHtml(artifact.title)+'</h1><p class="snapshot-note">阅读快照 · 版本 '+escapeHtml(artifact.revision)+' · 捕获于 '+escapeHtml(artifact.captured_at)+' · <a href="'+safeHref(artifact.official_url)+'" rel="noreferrer">原成品</a></p></header>'+warnings.map(w=>'<p class="notice">'+escapeHtml(w)+'</p>').join('')+(toc?'<nav class="directory" aria-label="目录"><strong>目录</strong><ul>'+toc+'</ul></nav>':'')+exportNode(artifact.content));
}
