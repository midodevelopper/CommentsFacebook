// In-memory Apps Script services for the v2 receiver (apps_script/src/*.gs), stricter than gas_fake.mjs:
//   * a grid with max rows/columns: reads and writes outside it throw, like Google's "outside the dimensions" error;
//   * numeric strings in non-text cells become Numbers; "=…" strings become formulas unless the cell is '@'
//     (or always, in formulaMode 'always', to exercise the apostrophe fallback); a leading apostrophe is consumed;
//   * Script Properties enforce 9 KB per value and 500 KB per store;
//   * Dates, notes, number formats, data validation, developer metadata, sheet copies, slicers/pivots/charts (config only).
// It is a model of what the code relies on, not Google's implementation.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const SRC = path.resolve(HERE, '..', '..', 'apps_script', 'src');
export const KEYS = { admin: 'test-admin-key', ingest: 'test-ingest-key' };
export const PROD = 'prod-spreadsheet-id';
const NUMERIC = /^-?\d+(\.\d+)?$/;
export const sha256 = s => crypto.createHash('sha256').update(String(s), 'utf8').digest('hex');

let gidSeq = 1000;

class Meta { constructor(k, v) { this.k = k; this.v = v; } getKey() { return this.k; } getValue() { return this.v; } }

export class FakeSheet {
  constructor(ss, name, opts = {}) {
    this.ss = ss; this.name = name; this.id = opts.gid !== undefined ? opts.gid : gidSeq++;
    this.maxRows = opts.maxRows || 1000; this.maxCols = opts.maxCols || 26;
    this.cells = new Map();   // "r,c" -> {v, f (formula), fmt, note, dv}
    this.meta = []; this.hidden = false; this.frozenRows = 0; this.frozenCols = 0;
    this.cf = []; this.charts = []; this.slicers = []; this.pivots = []; this.widths = {};
  }
  key(r, c) { return `${r},${c}`; }
  cell(r, c) { return this.cells.get(this.key(r, c)) || {}; }
  put(r, c, patch) {
    if (r < 1 || c < 1 || r > this.maxRows || c > this.maxCols) {
      throw new Error(`The coordinates of the range are outside the dimensions of the sheet (${r},${c} > ${this.maxRows}x${this.maxCols})`);
    }
    const k = this.key(r, c);
    const cur = Object.assign({}, this.cells.get(k) || {}, patch);
    if (cur.v === '' && !cur.f && !cur.fmt && !cur.note && !cur.dv) this.cells.delete(k); else this.cells.set(k, cur);
  }
  write(r, c, v) {
    const cur = this.cell(r, c);
    const text = cur.fmt === '@';
    let f = '';
    if (typeof v === 'string') {
      if (v.charAt(0) === "'") v = v.slice(1);
      else if (v.charAt(0) === '=' && (!text || this.ss.app.formulaMode === 'always')) { f = v; v = '#FORMULA'; }
      else if (!text && NUMERIC.test(v.trim())) v = Number(v);
    }
    this.put(r, c, { v: v, f: f });
  }
  getName() { return this.name; }
  setName(n) { this.name = n; return this; }
  getSheetId() { return this.id; }
  getParent() { return this.ss; }
  getMaxRows() { return this.maxRows; }
  getMaxColumns() { return this.maxCols; }
  insertRowsAfter(after, n) { this.maxRows += n; return this; }
  insertColumnsAfter(after, n) { this.maxCols += n; return this; }
  getLastRow() {
    let m = 0;
    for (const [k, c] of this.cells) if ((c.v !== '' && c.v !== undefined) || c.f) m = Math.max(m, Number(k.split(',')[0]));
    return m;
  }
  getLastColumn() {
    let m = 0;
    for (const [k, c] of this.cells) if ((c.v !== '' && c.v !== undefined) || c.f) m = Math.max(m, Number(k.split(',')[1]));
    return m;
  }
  getRange(a, b, nr = 1, nc = 1) {
    if (typeof a === 'string') {
      const m = /^([A-Z]+)(\d+)?(?::([A-Z]+)(\d+)?)?$/.exec(a);
      if (!m) throw new Error('fake A1 not supported: ' + a);
      const col = s => s.split('').reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0);
      const r1 = m[2] ? Number(m[2]) : 1, c1 = col(m[1]);
      const c2 = m[3] ? col(m[3]) : c1, r2 = m[4] ? Number(m[4]) : (m[3] ? this.maxRows : r1);
      return new FakeRange(this, r1, c1, r2 - r1 + 1, c2 - c1 + 1);
    }
    return new FakeRange(this, a, b, nr, nc);
  }
  appendRow(values) { const r = this.getLastRow() + 1; values.forEach((v, i) => this.write(r, i + 1, v)); return this; }
  deleteRow(r) {
    const moved = new Map();
    for (const [k, c] of this.cells) { const [rr, cc] = k.split(',').map(Number); if (rr < r) moved.set(k, c); else if (rr > r) moved.set(`${rr - 1},${cc}`, c); }
    this.cells = moved; this.maxRows -= 1;
  }
  hideSheet() { this.hidden = true; return this; }
  isSheetHidden() { return this.hidden; }
  setFrozenRows(n) { this.frozenRows = n; }
  setFrozenColumns(n) { this.frozenCols = n; }
  getFrozenRows() { return this.frozenRows; }
  addDeveloperMetadata(k, v) { this.meta.push(new Meta(k, v)); return this; }
  getDeveloperMetadata() { return this.meta.slice(); }
  copyTo(ss) {
    const copy = ss.insertSheet(`Copy of ${this.name}`);
    copy.maxRows = this.maxRows; copy.maxCols = this.maxCols;
    for (const [k, c] of this.cells) copy.cells.set(k, Object.assign({}, c));
    copy.frozenRows = this.frozenRows;
    return copy;
  }
  getProtections() { return []; }
  getConditionalFormatRules() { return this.cf.slice(); }
  setConditionalFormatRules(rules) { this.cf = rules.slice(); }
  getCharts() { return this.charts.slice(); }
  newChart() { return new ChartBuilder(); }
  insertChart(c) { this.charts.push(c); }
  insertSlicer(range, row, col) { const s = new FakeSlicer(range); this.slicers.push(s); return s; }
  getSlicers() { return this.slicers.slice(); }
  autoResizeColumns() { return this; }
  getColumnWidth(c) { return this.widths[c] || 100; }
  setColumnWidth(c, w) { this.widths[c] = w; return this; }
  // test helpers
  table() {
    const rows = this.getLastRow(), cols = this.getLastColumn();
    return Array.from({ length: rows }, (_, r) => Array.from({ length: cols }, (_, c) => this.cell(r + 1, c + 1).v ?? ''));
  }
  records() { const [h, ...rows] = this.table(); return rows.map(r => Object.fromEntries(h.map((k, i) => [k, r[i]]))); }
}

class ChartBuilder {
  constructor() { this.ranges = []; this.opts = {}; }
  setChartType(t) { this.type = t; return this; }
  addRange(r) { this.ranges.push(r); return this; }
  setPosition() { return this; }
  setOption(k, v) { this.opts[k] = v; return this; }
  build() { const ranges = this.ranges, type = this.type; return { getRanges: () => ranges, getType: () => type }; }
}

class FakeSlicer {
  constructor(range) { this.range = range; this.col = null; this.criteria = null; }
  setColumnFilterCriteria(col, crit) { this.col = col; this.criteria = crit; return this; }
  getColumnPosition() { return this.col; }
  setTitle(t) { this.title = t; return this; }
}

class FakePivot {
  constructor(anchor, source) { this.anchor = anchor; this.source = source; this.rows = []; this.values = []; this.filters = []; }
  addRowGroup(c) { const g = { col: c, sortBy() { return g; }, sortDescending() { return g; }, sortAscending() { return g; } }; this.rows.push(g); return g; }
  addPivotValue(c, fn) { const v = { col: c, fn: fn }; this.values.push(v); return v; }
  getPivotValues() { return this.values; }
  addFilter(c, crit) { this.filters.push({ col: c, crit: crit }); return this; }
}

class FakeRange {
  constructor(sheet, r, c, nr, nc) {
    if (r < 1 || c < 1 || nr < 1 || nc < 1) throw new Error(`invalid range ${r},${c},${nr},${nc}`);
    if (r + nr - 1 > sheet.maxRows || c + nc - 1 > sheet.maxCols) {
      throw new Error(`The coordinates of the range are outside the dimensions of the sheet (${r + nr - 1}x${c + nc - 1} > ${sheet.maxRows}x${sheet.maxCols})`);
    }
    Object.assign(this, { sheet, r, c, nr, nc });
  }
  each(fn) { for (let i = 0; i < this.nr; i++) for (let j = 0; j < this.nc; j++) fn(this.r + i, this.c + j, i, j); }
  grid(get) { return Array.from({ length: this.nr }, (_, i) => Array.from({ length: this.nc }, (_, j) => get(this.sheet.cell(this.r + i, this.c + j)))); }
  getSheet() { return this.sheet; }
  getRow() { return this.r; } getColumn() { return this.c; } getNumRows() { return this.nr; } getNumColumns() { return this.nc; }
  getA1Notation() { const L = n => { let s = ''; while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); } return s; }; return `${L(this.c)}${this.r}:${L(this.c + this.nc - 1)}${this.r + this.nr - 1}`; }
  getValues() { return this.grid(c => c.v === undefined ? '' : c.v); }
  getValue() { const v = this.sheet.cell(this.r, this.c).v; return v === undefined ? '' : v; }
  getFormulas() { return this.grid(c => c.f || ''); }
  getNotes() { return this.grid(c => c.note || ''); }
  getNumberFormats() { return this.grid(c => c.fmt || 'General'); }
  getDataValidations() { return this.grid(c => c.dv || null); }
  getDataValidation() { return this.sheet.cell(this.r, this.c).dv || null; }
  setValues(values) {
    if (values.length !== this.nr || values.some(row => row.length !== this.nc)) throw new Error(`setValues dimension mismatch: range ${this.nr}x${this.nc}`);
    this.each((r, c, i, j) => this.sheet.write(r, c, values[i][j]));
    return this;
  }
  setValue(v) { this.sheet.write(this.r, this.c, v); return this; }
  setFormula(f) { this.sheet.put(this.r, this.c, { f: f, v: '#FORMULA' }); return this; }
  setNotes(notes) { this.each((r, c, i, j) => this.sheet.put(r, c, { note: notes[i][j] || '' })); return this; }
  setNote(n) { this.sheet.put(this.r, this.c, { note: n || '' }); return this; }
  clearNote() { this.each((r, c) => this.sheet.put(r, c, { note: '' })); return this; }
  setNumberFormat(f) { this.each((r, c) => this.sheet.put(r, c, { fmt: f })); return this; }
  clearFormat() { this.each((r, c) => this.sheet.put(r, c, { fmt: '' })); return this; }
  clearContent() { this.each((r, c) => this.sheet.put(r, c, { v: '', f: '' })); return this; }
  setDataValidation(dv) { this.each((r, c) => this.sheet.put(r, c, { dv: dv })); return this; }
  clearDataValidations() { this.each((r, c) => this.sheet.put(r, c, { dv: null })); return this; }
  insertCheckboxes() {
    const dv = new FakeValidation('CHECKBOX', []);
    this.each((r, c) => { const cur = this.sheet.cell(r, c); this.sheet.put(r, c, { dv: dv, v: cur.v === 'TRUE' ? true : cur.v === 'FALSE' ? false : cur.v }); });
    return this;
  }
  copyTo(dest) {
    const src = [];
    this.each((r, c, i, j) => src.push([i, j, Object.assign({}, this.sheet.cell(r, c))]));
    src.forEach(([i, j, cell]) => dest.sheet.put(dest.r + i, dest.c + j, Object.assign({ v: '', f: '', fmt: '', note: '', dv: null }, cell)));
    return this;
  }
  offset(dr, dc) { return new FakeRange(this.sheet, this.r + dr, this.c + dc, this.nr, this.nc); }
  createPivotTable(source) { const p = new FakePivot(this, source); this.sheet.pivots.push(p); return p; }
}
for (const m of ['setBackground', 'setFontColor', 'setFontWeight', 'setFontFamily', 'setFontSize', 'setHorizontalAlignment',
  'setVerticalAlignment', 'setBorder', 'setWrap']) FakeRange.prototype[m] = function () { return this; };

class FakeValidation {
  constructor(type, values) { this.type = type; this.values = values; }
  getCriteriaType() { return this.type; }
  getCriteriaValues() { return this.values; }
}

export class FakeSpreadsheet {
  constructor(app, id, name) { this.app = app; this.id = id; this.name = name || id; this.sheets = []; this.tz = 'Asia/Riyadh'; this.meta = []; }
  getId() { return this.id; }
  getName() { return this.name; }
  getSpreadsheetTimeZone() { return this.tz; }
  getSheets() { return this.sheets.slice(); }
  getSheetByName(n) { return this.sheets.find(s => s.name === n) || null; }
  insertSheet(n, index, opts) {
    if (this.getSheetByName(n)) throw new Error(`A sheet with the name "${n}" already exists`);
    const sh = new FakeSheet(this, n, opts);
    if (index === undefined) this.sheets.push(sh); else this.sheets.splice(index, 0, sh);
    return sh;
  }
  deleteSheet(sh) { this.sheets = this.sheets.filter(s => s !== sh); }
  getNamedRanges() { return []; }
  getDeveloperMetadata() { return this.meta.slice(); }
}

class Builder {
  constructor(kind) { this.kind = kind; this.cfg = {}; }
  requireValueInList(v) { this.cfg = { type: 'VALUE_IN_LIST', values: [v] }; return this; }
  requireCheckbox() { this.cfg = { type: 'CHECKBOX', values: [] }; return this; }
  setAllowInvalid() { return this; }
  whenCellNotEmpty() { this.cfg = { type: 'NOT_EMPTY' }; return this; }
  whenTextEqualTo(t) { this.cfg = { type: 'EQ', value: t }; return this; }
  whenTextNotEqualTo(t) { this.cfg = { type: 'NE', value: t }; return this; }
  whenFormulaSatisfied(f) { this.cfg = { type: 'FORMULA', values: [f] }; return this; }
  setBackground() { return this; }
  setRanges(r) { this.cfg.ranges = r; return this; }
  build() {
    if (this.kind === 'dv') return new FakeValidation(this.cfg.type, this.cfg.values);
    const cfg = this.cfg;
    if (this.kind === 'cf') return { getBooleanCondition: () => ({ getCriteriaValues: () => cfg.values }) };
    return cfg;
  }
}

export class PropStore {
  constructor() { this.map = new Map(); this.writes = 0; this.failNext = 0; }
  bytes() { let n = 0; for (const [k, v] of this.map) n += k.length + v.length; return n; }
  getProperty(k) { return this.map.has(k) ? this.map.get(k) : null; }
  setProperty(k, v) {
    if (this.failNext > 0) { this.failNext--; throw new Error('Service invoked too many times: properties (injected)'); }
    v = String(v);
    if (Buffer.byteLength(v) > 9 * 1024) throw new Error('Argument too large: value');
    const before = this.map.get(k);
    this.map.set(k, v);
    if (this.bytes() > 500 * 1024) { if (before === undefined) this.map.delete(k); else this.map.set(k, before); throw new Error('You have exceeded the property storage quota'); }
    this.writes++;
    return this;
  }
  deleteProperty(k) { this.map.delete(k); return this; }
  getProperties() { return Object.fromEntries(this.map); }
  getKeys() { return [...this.map.keys()]; }
}

function formatDate(date, tz, fmt) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
    .formatToParts(date).map(p => [p.type, p.value]));
  if (fmt === 'u') return String(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(parts.weekday) + 1);
  return fmt.replace('yyyy', parts.year).replace('MM', parts.month).replace('dd', parts.day).replace('HH', parts.hour).replace('mm', parts.minute);
}

export function makeApp(opts = {}) {
  const app = { formulaMode: opts.formulaMode || 'text-safe', files: new Map(), props: new PropStore(), lockBusy: false, cache: new Map() };
  app.prod = new FakeSpreadsheet(app, PROD, 'face book Groups');
  app.files.set(PROD, app.prod);
  app.addFile = (id, name) => { const ss = new FakeSpreadsheet(app, id, name); app.files.set(id, ss); return ss; };
  return app;
}

export function loadV2(opts = {}) {
  const app = opts.app || makeApp(opts);
  const lock = { tries: 0 };
  const lockObj = () => ({ tryLock() { lock.tries++; return !app.lockBusy; }, releaseLock() {}, waitLock() {} });
  const config = Object.assign({ PRODUCTION_ID: PROD, ADMIN_HASH: sha256(KEYS.admin), INGEST_HASH: sha256(KEYS.ingest), CONFIG_VERSION: 1, POST_REPLIES: false }, opts.config || {});
  const calls = { tabAccess: 0 };
  const context = vm.createContext({
    CF_CONFIG: config, Date, JSON, Math, BigInt, Set, Object, Array, String, Number, Boolean, Error, RegExp, isNaN, console,
    SpreadsheetApp: {
      getActiveSpreadsheet: () => { calls.tabAccess++; return app.prod; },
      openById: id => { calls.tabAccess++; const f = app.files.get(id); if (!f) throw new Error('not found: ' + id); return f; },
      newDataValidation: () => new Builder('dv'), newFilterCriteria: () => new Builder('fc'), newConditionalFormatRule: () => new Builder('cf'),
      flush: () => {}, BorderStyle: { SOLID_THICK: 'SOLID_THICK' }, ProtectionType: { RANGE: 'RANGE', SHEET: 'SHEET' },
      PivotTableSummarizeFunction: { COUNTA: 'COUNTA' },
    },
    Charts: { ChartType: { COLUMN: 'COLUMN', BAR: 'BAR', PIE: 'PIE' } },
    PropertiesService: { getScriptProperties: () => app.props },
    LockService: { getScriptLock: lockObj, getDocumentLock: lockObj },
    CacheService: { getScriptCache: () => ({ put: (k, v) => app.cache.set(k, v), get: k => app.cache.get(k) || null }) },
    Utilities: {
      DigestAlgorithm: { SHA_256: 'sha256' }, Charset: { UTF_8: 'utf8' },
      computeDigest: (alg, text) => [...crypto.createHash('sha256').update(String(text), 'utf8').digest()].map(b => (b > 127 ? b - 256 : b)),
      formatDate: formatDate,
    },
    ContentService: { MimeType: { JSON: 'application/json' }, createTextOutput: content => ({ content, mime: null, setMimeType(m) { this.mime = m; return this; } }) },
  });
  for (const f of fs.readdirSync(SRC).filter(f => f.endsWith('.gs')).sort()) {
    vm.runInContext(fs.readFileSync(path.join(SRC, f), 'utf8'), context, { filename: f });
  }
  vm.runInContext('globalThis.__c = {POST_HEAD, COMMENT_HEAD, STATUSES, LEDGER_HEAD, ACTIONS, SETUP_ACTIONS, V1_POSTS, V1_COMMENTS};', context);
  let seq = 0;
  const post = (body, raw) => JSON.parse(context.doPost({ postData: { contents: raw !== undefined ? raw : JSON.stringify(body) } }).content);
  const call = (action, payload = {}, extra = {}) => post(Object.assign({ api_version: 2, request_id: `req-${++seq}`, action,
    auth: { kind: 'admin', key: extra.key || KEYS.admin }, payload }, extra.target !== undefined ? { target: extra.target } : {},
    extra.request_id ? { request_id: extra.request_id } : {}));
  const ingest = (payload, extra = {}) => post(Object.assign({ api_version: 2, request_id: extra.request_id || `ing-${++seq}`, action: 'ingest',
    auth: { kind: 'ingest', key: extra.key || KEYS.ingest }, payload }, extra.target !== undefined ? { target: extra.target } : {}));
  const get = parameter => JSON.parse(context.doGet({ parameter }).content);
  return { app, ss: app.prod, context, consts: context.__c, call, ingest, post, get, calls, lock, config };
}

// ------------------------------------------------------------------ fixtures

export function seedV1(ss, { posts = [], comments = [], extraPostCol, notes = [] } = {}) {
  const P = ['post_id', 'group_id', 'author', 'text', 'created_time', 'url', 'received_at', 'author_id', 'permalink', 'comment_sort', 'post content'];
  const C = ['comment_id', 'post_id', 'parent_id', 'author', 'text', 'created_time', 'received_at', 'group_id', 'reply_comment_id', 'is_reply', 'reply_target_id', 'author_id', 'author_url', 'permalink', 'reply', 'reply_status', 'post content'];
  const ps = ss.getSheetByName('Posts') || ss.insertSheet('Posts', undefined, { gid: 318060874 });
  const cs = ss.getSheetByName('Comments') || ss.insertSheet('Comments', undefined, { gid: 2107666404, maxRows: Math.max(1000, comments.length + 1) });
  const ph = extraPostCol ? P.concat([extraPostCol]) : P;
  const write = (sh, head, rows) => {
    sh.getRange(1, 1, 1, head.length).setValues([head]);
    if (!rows.length) return;
    const range = sh.getRange(2, 1, rows.length, head.length);
    range.setNumberFormat('@');
    range.setValues(rows.map(r => head.map(h => r[h] === undefined ? '' : r[h])));
    const recv = head.indexOf('received_at');
    sh.getRange(2, recv + 1, rows.length, 1).setNumberFormat('yyyy/mm/dd');
  };
  write(ps, ph, posts);
  write(cs, C, comments);
  notes.forEach(([tab, r, c, n]) => ss.getSheetByName(tab).getRange(r, c).setNote(n));
  return { ps, cs };
}

export function v1Post(id, extra = {}) {
  return Object.assign({ post_id: id, group_id: '1111111111111111', author: 'A', text: 'caption ' + id,
    created_time: '2026-10-22 13:05', url: `https://www.facebook.com/groups/1111111111111111/posts/${id}/`,
    received_at: new Date(Date.UTC(2026, 9, 22, 13, 20, 11)), author_id: '100000000000099',
    permalink: `https://www.facebook.com/groups/1111111111111111/posts/${id}/`, comment_sort: 'الأحدث', 'post content': 'caption ' + id }, extra);
}

export function v1Comment(id, postId, extra = {}) {
  return Object.assign({ comment_id: id, post_id: postId, parent_id: '', author: 'U', text: 't' + id, created_time: '2026-10-22 12:58',
    received_at: new Date(Date.UTC(2026, 9, 22, 13, 21, 0)), group_id: '1111111111111111', reply_comment_id: '', is_reply: '',
    reply_target_id: id, author_id: '100000000000001', author_url: 'https://www.facebook.com/groups/1111111111111111/user/100000000000001/',
    permalink: `https://www.facebook.com/groups/1111111111111111/posts/${postId}/?comment_id=${id}`, reply: '', reply_status: '',
    'post content': 'caption ' + postId }, extra);
}
