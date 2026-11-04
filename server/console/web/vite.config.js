import {defineConfig} from 'vite';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

const source=readFileSync(new URL('./src/startup-guard.js',import.meta.url),'utf8');
const filename='assets/startup-'+createHash('sha256').update(source).digest('hex').slice(0,12)+'.js';
export default defineConfig({
 build:{target:'chrome90'},
 plugins:[{
  name:'console-startup-recovery',
  generateBundle(){this.emitFile({type:'asset',fileName:filename,source});},
  transformIndexHtml:{order:'post',handler(html){return html.replace('<script type="module"',
   '<script async src="/'+filename+'" data-cfasync="false"></script>\n<script data-cfasync="false" type="module"');}}
 }]
});
