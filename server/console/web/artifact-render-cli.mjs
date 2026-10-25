import {artifactHtml,htmlPage,escapeHtml} from './src/artifactExport.js';
let raw='';for await(const part of process.stdin){raw+=part;if(raw.length>24*1024*1024)throw Error('export_limit');}
const value=JSON.parse(raw);
if(value.action==='document')process.stdout.write(artifactHtml(value.artifact));
else if(value.action==='index'){
 const m=value.manifest;
 const rows=m.artifacts.map(a=>'<tr><td>'+escapeHtml(a.session)+'</td><td>'+(a.file?'<a href="'+escapeHtml(a.file)+'">'+escapeHtml(a.title)+'</a>':escapeHtml(a.title))+'</td><td>'+escapeHtml(a.status==='document'?'HTML 文档快照':a.status==='original_html'?'原始互动 HTML':'仅保留链接')+'</td><td>'+escapeHtml([a.reason||'',a.figures!=null?`插图/媒体 ${a.available_figures}/${a.figures}`:'',a.snapshot_outdated?`保存版本 ${a.revision}；记录中还有版本 ${a.latest_observed_revision}`:'',a.stale_figures?`${a.stale_figures} 张图有后续修订`:'',a.retained?'本次没有新的完整副本，保留上次文件':''].filter(Boolean).join('；'))+'</td><td><a href="'+escapeHtml(a.official_url)+'" rel="noreferrer">原成品</a></td></tr>').join('');
 process.stdout.write(htmlPage('Claude 会话成品目录','<h1>Claude 会话成品</h1><p>'+m.session_count+' 个 Remote Control 会话 · '+m.html_count+' 份 HTML · '+m.link_only_count+' 项仅有链接</p><p class="snapshot-note">快照生成于 '+escapeHtml(m.created_at)+'。目录中的副本不会自动改写，可回控制台更新全部快照。</p><p>文档图像和公式已经内嵌。原始互动 HTML 保留自身脚本；缺少内容、较早版本和保留的旧副本均列明。</p><div class="artifact-table"><table><thead><tr><th>会话</th><th>成品</th><th>格式</th><th>说明</th><th>原页</th></tr></thead><tbody>'+rows+'</tbody></table></div>'));
}else throw Error('unknown_export_action');
