// In-memory stand-ins for the Apps Script services facebook_sheet_receiver.gs uses,
// and a loader that runs the real .gs file against them in a fresh vm context.
//
// Number-format emulation matters here: Google Sheets turns a numeric-looking
// string into a Number unless the cell is formatted as text ('@'). The receiver
// relies on setNumberFormat('@') to keep 16-19 digit IDs exact, so the fake
// reproduces that coercion (and its precision loss) for unformatted cells.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const GS_PATH = path.resolve(HERE, '..', '..', 'facebook_sheet_receiver.gs');

const NUMERIC = /^-?\d+(\.\d+)?$/;

export class FakeSheet {
  constructor(name) {
    this.name = name;
    this.data = [];            // rows of cell values
    this.formats = {};         // "r,c" -> format
    this.columnFormats = {};   // c -> format
    this.frozenRows = 0;
    this.deleted = [];
  }
  getName() { return this.name; }
  getLastRow() {
    for (let r = this.data.length; r >= 1; r--) {
      if ((this.data[r - 1] || []).some(v => v !== '' && v !== null && v !== undefined)) return r;
    }
    return 0;
  }
  getLastColumn() {
    let max = 0;
    this.data.forEach(row => row.forEach((v, i) => { if (v !== '' && v !== null && v !== undefined) max = Math.max(max, i + 1); }));
    return max;
  }
  cell(r, c) { const row = this.data[r - 1]; return row && row[c - 1] !== undefined ? row[c - 1] : ''; }
  format(r, c) { return this.formats[`${r},${c}`] || this.columnFormats[c] || ''; }
  write(r, c, v) {
    while (this.data.length < r) this.data.push([]);
    const row = this.data[r - 1];
    while (row.length < c) row.push('');
    if (typeof v === 'string' && NUMERIC.test(v.trim()) && this.format(r, c) !== '@') v = Number(v);
    row[c - 1] = v;
  }
  getRange(a, b, nr = 1, nc = 1) {
    if (typeof a === 'string') {
      const m = /^([A-Z]+):\1$/.exec(a);
      if (!m) throw new Error('fake only supports whole-column A1 ranges, got ' + a);
      return new FakeColumn(this, m[1].charCodeAt(0) - 64);
    }
    return new FakeRange(this, a, b, nr, nc);
  }
  appendRow(values) { const r = this.getLastRow() + 1; values.forEach((v, i) => this.write(r, i + 1, v)); }
  deleteRow(r) { this.deleted.push(r); this.data.splice(r - 1, 1); }
  setFrozenRows(n) { this.frozenRows = n; }
  // test helpers
  table() { const w = this.getLastColumn(); return this.data.slice(0, this.getLastRow()).map(r => Array.from({length: w}, (_, i) => r[i] === undefined ? '' : r[i])); }
  records() { const [head, ...rows] = this.table(); return rows.map(r => Object.fromEntries(head.map((h, i) => [h, r[i]]))); }
}

class FakeColumn {
  constructor(sheet, c) { this.sheet = sheet; this.c = c; }
  setNumberFormat(f) { this.sheet.columnFormats[this.c] = f; return this; }
}

class FakeRange {
  constructor(sheet, r, c, nr, nc) {
    if (r < 1 || c < 1 || nr < 1 || nc < 1) throw new Error(`invalid range ${r},${c},${nr},${nc}`);
    Object.assign(this, {sheet, r, c, nr, nc});
  }
  getValues() {
    return Array.from({length: this.nr}, (_, i) => Array.from({length: this.nc}, (_, j) => this.sheet.cell(this.r + i, this.c + j)));
  }
  getValue() { return this.sheet.cell(this.r, this.c); }
  setValues(values) {
    if (values.length !== this.nr || values.some(row => row.length !== this.nc)) {
      throw new Error(`setValues dimension mismatch: range ${this.nr}x${this.nc}`);
    }
    values.forEach((row, i) => row.forEach((v, j) => this.sheet.write(this.r + i, this.c + j, v)));
    return this;
  }
  setValue(v) { this.sheet.write(this.r, this.c, v); return this; }
  setNumberFormat(f) {
    for (let i = 0; i < this.nr; i++) for (let j = 0; j < this.nc; j++) this.sheet.formats[`${this.r + i},${this.c + j}`] = f;
    return this;
  }
  setFontWeight() { return this; }
}

export class FakeSpreadsheet {
  constructor() { this.sheets = {}; }
  getSheetByName(n) { return this.sheets[n] || null; }
  insertSheet(n) { return (this.sheets[n] = new FakeSheet(n)); }
}

export function loadReceiver({ lockTimeout = false } = {}) {
  const ss = new FakeSpreadsheet();
  const lock = { waits: [], released: 0 };
  const context = vm.createContext({
    SpreadsheetApp: { getActiveSpreadsheet: () => ss },
    LockService: {
      getScriptLock: () => ({
        waitLock(ms) { lock.waits.push(ms); if (lockTimeout) throw new Error('Lock timeout'); },
        releaseLock() { lock.released++; },
      }),
    },
    ContentService: {
      MimeType: { JSON: 'application/json' },
      createTextOutput: content => ({ content, mime: null, setMimeType(m) { this.mime = m; return this; } }),
    },
  });
  const source = fs.readFileSync(GS_PATH, 'utf8');
  // top-level const bindings aren't properties of the vm global; export them explicitly
  vm.runInContext(source + '\n;globalThis.__consts = {TOKEN, POSTS, COMMENTS, POST_HEAD, COMMENT_HEAD, KEEP_IF_BLANK};',
    context, { filename: GS_PATH });
  const call = (fn, ...args) => context[fn](...args);
  const post = body => JSON.parse(context.doPost({ postData: { contents: typeof body === 'string' ? body : JSON.stringify(body) } }).content);
  const get = parameter => JSON.parse(context.doGet({ parameter }).content);
  return { ss, lock, context, consts: context.__consts, call, post, get };
}
