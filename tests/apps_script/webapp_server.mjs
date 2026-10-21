// Serves the real facebook_sheet_receiver.gs over loopback HTTP, like a deployed /exec web app,
// backed by the in-memory sheet from gas_fake.mjs. Used by tests/test_end_to_end.py.
//   GET  /exec?...      -> doGet({parameter})
//   POST /exec          -> doPost({postData: {contents}})
//   GET  /__state       -> {sheetName: table}
//   POST /__set         -> {sheet, key, column, value}: edit a cell like the owner would
// Prints "PORT <n>" on stdout once listening.
import http from 'node:http';
import { loadReceiver } from './gas_fake.mjs';

const receiver = loadReceiver();

function send(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(body);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const chunks = [];
  req.on('data', c => chunks.push(c));
  req.on('end', () => {
    const raw = Buffer.concat(chunks).toString('utf8');
    try {
      if (url.pathname === '/exec' && req.method === 'GET') {
        const out = receiver.context.doGet({ parameter: Object.fromEntries(url.searchParams) });
        res.writeHead(200, { 'Content-Type': out.mime });
        return res.end(out.content);
      }
      if (url.pathname === '/exec' && req.method === 'POST') {
        const out = receiver.context.doPost({ postData: { contents: raw } });
        res.writeHead(200, { 'Content-Type': out.mime });
        return res.end(out.content);
      }
      if (url.pathname === '/__state') {
        const state = {};
        for (const [name, sheet] of Object.entries(receiver.ss.sheets)) state[name] = sheet.table();
        return send(res, 200, state);
      }
      if (url.pathname === '/__set') {
        const { sheet, key, column, value } = JSON.parse(raw);
        const sh = receiver.ss.getSheetByName(sheet);
        const cols = receiver.call('headerMap_', sh);
        const row = receiver.call('findIdRow_', sh, String(key));
        if (!row) return send(res, 404, { ok: false, error: 'no row ' + key });
        sh.getRange(row, cols[column] + 1).setValue(value);
        return send(res, 200, { ok: true, row });
      }
      send(res, 404, { ok: false, error: 'not found' });
    } catch (err) {
      // what Apps Script does on an uncaught exception: an HTML error page
      res.writeHead(500, { 'Content-Type': 'text/html' });
      res.end('<html><body>Script error: ' + String(err) + '</body></html>');
    }
  });
});

server.listen(0, '127.0.0.1', () => {
  process.stdout.write(`PORT ${server.address().port}\n`);
});
