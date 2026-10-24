// Serves the real v2 receiver (apps_script/src/*.gs) over loopback HTTP, like a deployed /exec web app,
// backed by gas_fake_v2.mjs. The production spreadsheet starts empty at schema v2 (as after the cut-over).
// Used by tests/test_end_to_end.py.
//   GET  /exec          -> doGet({parameter})
//   POST /exec          -> doPost({postData: {contents}})
//   GET  /__state       -> {sheetName: table}   (Dates as ISO strings)
//   POST /__set         -> {sheet, key, column, value}: edit a cell like the owner would (no onEdit, like an API edit)
// Prints "PORT <n>" on stdout once listening.
import http from 'node:http';
import { loadV2, PROD } from './gas_fake_v2.mjs';

const r = loadV2();
const ps = r.ss.insertSheet('Posts', undefined, { gid: 318060874 });
const cs = r.ss.insertSheet('Comments', undefined, { gid: 2107666404 });
ps.getRange(1, 1, 1, r.consts.POST_HEAD.length).setValues([[...r.consts.POST_HEAD]]);
cs.getRange(1, 1, 1, r.consts.COMMENT_HEAD.length).setValues([[...r.consts.COMMENT_HEAD]]);
const ns = r.context.nsOf_(PROD);
r.app.props.setProperty(`ns.${ns}.SCHEMA_VERSION`, '2');

const plain = v => (v instanceof Date ? v.toISOString() : v);

function send(res, status, obj) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(obj));
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const chunks = [];
  req.on('data', c => chunks.push(c));
  req.on('end', () => {
    const raw = Buffer.concat(chunks).toString('utf8');
    try {
      if (url.pathname === '/exec' && req.method === 'GET') {
        const out = r.context.doGet({ parameter: Object.fromEntries(url.searchParams) });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end(out.content);
      }
      if (url.pathname === '/exec' && req.method === 'POST') {
        const out = r.context.doPost({ postData: { contents: raw } });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end(out.content);
      }
      if (url.pathname === '/__state') {
        const state = {};
        for (const sh of r.ss.getSheets()) state[sh.getName()] = sh.table().map(row => row.map(plain));
        return send(res, 200, state);
      }
      if (url.pathname === '/__set') {
        const { sheet, key, column, value } = JSON.parse(raw);
        const sh = r.ss.getSheetByName(sheet);
        const table = sh.table();
        const head = table[0];
        const idCol = sheet === 'Posts' ? 'post_id' : 'comment_id';
        const row = table.findIndex(x => String(x[head.indexOf(idCol)]) === String(key));
        if (row < 1) return send(res, 404, { ok: false, error: 'no row ' + key });
        sh.getRange(row + 1, head.indexOf(column) + 1).setValue(value);
        return send(res, 200, { ok: true, row: row + 1 });
      }
      send(res, 404, { ok: false, error: 'not found' });
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'text/html' });
      res.end('<html><body>Script error: ' + String(err) + '</body></html>');
    }
  });
});

server.listen(0, '127.0.0.1', () => {
  process.stdout.write(`PORT ${server.address().port}\n`);
});
