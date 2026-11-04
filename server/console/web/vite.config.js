import {defineConfig,normalizePath} from 'vite';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const source=readFileSync(new URL('./src/startup-guard.js',import.meta.url),'utf8');
const filename='assets/startup-'+createHash('sha256').update(source).digest('hex').slice(0,12)+'.js';
const richEntry=fileURLToPath(new URL('./src/RichMarkdown.jsx',import.meta.url));
export default defineConfig({
 build:{target:'chrome90'},
 plugins:[{
  name:'console-startup-recovery',
  buildStart(){this.emitFile({type:'chunk',id:richEntry,name:'rich-text',preserveSignature:'strict'});},
  generateBundle(){this.emitFile({type:'asset',fileName:filename,source});},
  transformIndexHtml:{order:'post',handler(html,context){
   const rich=context.bundle&&Object.values(context.bundle).find(c=>c.type==='chunk'&&c.facadeModuleId&&normalizePath(c.facadeModuleId)===normalizePath(richEntry));
   if(context.bundle&&!rich)throw Error('rich_renderer_entry_missing');
   return {html:html.replace('<script type="module"',
    '<script async src="/'+filename+'" data-cfasync="false"></script>\n<script data-cfasync="false" type="module"'),
    tags:[{tag:'meta',attrs:{name:'console-rich-module',content:rich?'/'+rich.fileName:'/src/RichMarkdown.jsx'},injectTo:'head'}]};
  }}
 }]
});
