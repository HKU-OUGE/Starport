import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {BasicMarkdown} from './BasicMarkdown.js';
const render=props=>renderToStaticMarkup(React.createElement(BasicMarkdown,props));
test('basic messages retain headings, emphasis, code, tables and task lists without rich modules',()=>{
 const html=render({text:'# Result\n\n**Ready** and `code`.\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n- [x] Done\n\n```python\nprint(1)\n```'});
 for(const token of ['<h1>Result</h1>','<strong>Ready</strong>','<code>code</code>','<table>','<th>A</th>','<td>2</td>','type="checkbox"','print(1)'])assert.ok(html.includes(token),token);
});
test('raw HTML and unsafe links do not execute; local files require a registered attachment',()=>{
 const html=render({text:'<script>alert(1)</script>\n\n[bad](javascript:alert) [local](/private/report.pdf) [good](https://example.invalid/)'});
 assert.ok(!html.includes('<script'));assert.ok(!html.includes('href="javascript:'));assert.ok(!html.includes('href="/private/'));assert.ok(html.includes('href="https://example.invalid/"'));
 const file=render({text:'[report](report.pdf)',session:'session-fixture',attachments:[{id:'opaque-file',reference:'report.pdf',mime:'application/pdf',available:true,source:'document'}]});
 assert.ok(file.includes('/api/documents/session-fixture/opaque-file'));
});
