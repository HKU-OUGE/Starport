import {cp, mkdir, readFile} from 'node:fs/promises';
const root = new URL('./node_modules/pdfjs-dist/', import.meta.url);
const {version} = JSON.parse(await readFile(new URL('package.json', root), 'utf8'));
const output = new URL(`./public/pdf-assets/${version}/`, import.meta.url);
await mkdir(output, {recursive: true});
for (const name of ['cmaps', 'standard_fonts', 'LICENSE']) {
  await cp(new URL(name, root), new URL(name, output), {recursive: true});
}
