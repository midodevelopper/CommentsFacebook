/**
 * أوامر الأدمن: قايمة التجربة، الجرد، البصمات، الترحيل (converge-in-place)، الرجوع، التنسيق، الداشبورد، التحقق.
 * كل أمر بيشتغل على target صريح، والترحيل والرجوع بيقروا حالة الملف الفعلية قبل أي خطوة (§3.4) — مش الـ checkpoint.
 * أي شيت بيتعمل هنا عليه developer metadata «cf.created»، ومابيتمسحش غير بالـ gid والعلامة مع بعض.
 */
const V1_POSTS = ['post_id', 'group_id', 'author', 'text', 'created_time', 'url', 'received_at', 'author_id',
  'permalink', 'comment_sort', 'post content'];
const V1_COMMENTS = ['comment_id', 'post_id', 'parent_id', 'author', 'text', 'created_time', 'received_at', 'group_id',
  'reply_comment_id', 'is_reply', 'reply_target_id', 'author_id', 'author_url', 'permalink', 'reply', 'reply_status',
  'post content'];
const MARKER = 'cf.created';
const LEGACY_STATUS = {'': '', posted: 'sent', failed: 'failed', declined: 'declined'};
const HEADER_FILL = '#1F2937';
const CATEGORY = {
  ids: {cols: ['post_id', 'group_id', 'comment_id', 'parent_id', 'reply_comment_id', 'reply_target_id', 'author_id', 'nest_parent_id'], color: '#F3F4F6'},
  names: {cols: ['author', 'group_name'], color: '#E0F2FE'},
  content: {cols: ['text', 'media_url', 'post_text', 'mentions', 'comment_sort'], color: '#D1D5DB'},
  dates: {cols: ['created_time', 'received_at'], color: '#DCFCE7'},
  urls: {cols: ['url', 'author_url'], color: '#EDE9FE'},
  reply: {cols: ['reply', 'reply_status', 'is_reply'], color: '#FEF3C7'}
};
const WRAP = ['text', 'post_text', 'media_url', 'reply'];

// ------------------------------------------------------------------ أدوات

function marked_(sheet, value) {
  return sheet.getDeveloperMetadata().some(m => m.getKey() === MARKER && m.getValue() === value);
}

function findMarked_(ss, value) {
  return ss.getSheets().find(s => marked_(s, value)) || null;
}

function sheetByGid_(ss, gid) { return ss.getSheets().find(s => s.getSheetId() === Number(gid)) || null; }

function cellSig_(v) {
  if (v instanceof Date) return 'd' + v.getTime();
  if (typeof v === 'number') return 'n' + v;
  if (typeof v === 'boolean') return 'b' + v;
  return 's' + String(v);
}

// بصمة المحتوى: القيم بنوعها + الملاحظات + تنسيق الأرقام. للمقارنة بين الأصل والنسخة والشيت المؤقت.
function contentHash_(sheet) {
  const rows = Math.max(sheet.getLastRow(), 1), cols = Math.max(sheet.getLastColumn(), 1);
  const range = sheet.getRange(1, 1, rows, cols);
  const values = range.getValues(), notes = range.getNotes(), formats = range.getNumberFormats();
  const parts = [];
  for (let start = 0; start < rows; start += 2000) {
    const chunk = [];
    for (let r = start; r < Math.min(rows, start + 2000); r++) {
      chunk.push(values[r].map(cellSig_).join('␟') + '␞' + notes[r].join('␟') + '␞' + formats[r].join('␟'));
    }
    parts.push(sha256Hex_(chunk.join('\n')));
  }
  return sha256Hex_(`${rows}x${cols}|` + parts.join(','));
}

function deepHash_(sheet) {
  const rows = Math.max(sheet.getLastRow(), 1), cols = Math.max(sheet.getLastColumn(), 1);
  const range = sheet.getRange(1, 1, rows, cols);
  const formulas = range.getFormulas().map(r => r.join('␟')).join('\n');
  const validations = range.getDataValidations().map(r => r.map(v => v ? v.getCriteriaType() + ':' + JSON.stringify(v.getCriteriaValues()) : '').join('␟')).join('\n');
  const meta = sheet.getDeveloperMetadata().map(m => m.getKey() + '=' + m.getValue()).sort().join(';');
  return sha256Hex_([contentHash_(sheet), sha256Hex_(formulas), sha256Hex_(validations), sheet.getName(),
    sheet.getSheetId(), sheet.getFrozenRows(), meta].join('|'));
}

// ------------------------------------------------------------------ قايمة التجربة والأعلام

function actionAllowTarget_(ctx) {
  const id = String(ctx.payload.target_id || '');
  const mode = ctx.payload.mode === 'ro' ? 'ro' : 'rw';
  if (!id || id === CF_CONFIG.PRODUCTION_ID) throw new ApiErr('E_TARGET_NOT_ALLOWED', 'production can never be a rehearsal target');
  const title = SpreadsheetApp.openById(id).getName();
  const prefix = mode === 'rw' ? 'CF-rehearsal-' : 'CF-backup-';
  if (title.indexOf(prefix) !== 0) throw new ApiErr('E_TARGET_NOT_ALLOWED', `title must start with ${prefix}`);
  const list = allowlist_();
  list[id] = {mode: mode, expires: Date.now() + 24 * 3600 * 1000, title: title};
  globalSet_('REHEARSAL_ALLOWLIST', JSON.stringify(list));
  return {allowed: true, mode: mode};
}

function actionRevokeTarget_(ctx) {
  const list = allowlist_();
  const id = String(ctx.payload.target_id || '');
  const had = !!list[id];
  delete list[id];
  globalSet_('REHEARSAL_ALLOWLIST', JSON.stringify(list));
  if (ctx.payload.clear_namespace && id !== CF_CONFIG.PRODUCTION_ID) clearNamespace_(nsOf_(id));
  return {revoked: had};
}

function clearNamespace_(ns) {
  const p = props_();
  Object.keys(p.getProperties()).filter(k => k.indexOf(`ns.${ns}.`) === 0).forEach(k => p.deleteProperty(k));
}

function actionSetFlags_(ctx) {
  const p = ctx.payload;
  if (p.maintenance !== undefined) nsSet_(ctx.ns, 'MAINTENANCE', p.maintenance ? 'true' : 'false');
  if (p.replies_kill !== undefined) globalSet_('REPLIES_KILL', p.replies_kill ? 'true' : 'false');
  if (p.formula_mode !== undefined) {
    if (['literal', 'apostrophe'].indexOf(p.formula_mode) === -1) throw new ApiErr('E_VALIDATION', 'formula_mode');
    globalSet_('FORMULA_MODE', p.formula_mode);
  }
  return flags_(ctx);
}

function flags_(ctx) {
  return {schema_version: schemaOf_(ctx.ns), maintenance: inMaintenance_(ctx.ns), replies_killed: repliesKilled_(),
    formula_mode: globalGet_('FORMULA_MODE') || 'apostrophe', migration_run: nsGet_(ctx.ns, 'MIGRATION_RUN') || ''};
}

function actionNamespaceDump_(ctx) {
  const all = props_().getProperties();
  const keys = Object.keys(all).filter(k => k.indexOf(`ns.${ctx.ns}.`) === 0).sort();
  return {keys: keys.length, sha256: sha256Hex_(keys.map(k => k + '=' + all[k]).join('\n'))};
}

// ------------------------------------------------------------------ جرد وبصمات وقدرات

function actionInventory_(ctx) {
  const ss = ctx.ss;
  const sheets = ss.getSheets().map(sh => {
    const rows = Math.max(sh.getLastRow(), 1), cols = Math.max(sh.getLastColumn(), 1);
    const range = sh.getRange(1, 1, rows, cols);
    const formulas = range.getFormulas();
    const notes = range.getNotes();
    const head = sh.getRange(1, 1, 1, cols).getValues()[0].map(String);
    const out = {name: sh.getName(), gid: sh.getSheetId(), max_rows: sh.getMaxRows(), max_cols: sh.getMaxColumns(),
      last_row: sh.getLastRow(), last_col: sh.getLastColumn(), frozen_rows: sh.getFrozenRows(), hidden: sh.isSheetHidden(),
      head: head, formulas: formulas.reduce((n, r) => n + r.filter(Boolean).length, 0),
      formula_refs: formulas.reduce((n, r) => n + r.filter(f => /Posts|Comments/.test(f)).length, 0),
      notes: notes.reduce((n, r) => n + r.filter(Boolean).length, 0),
      validations: range.getDataValidations().reduce((n, r) => n + r.filter(Boolean).length, 0),
      protections: sh.getProtections(SpreadsheetApp.ProtectionType.RANGE).length + sh.getProtections(SpreadsheetApp.ProtectionType.SHEET).length,
      conditional_formats: sh.getConditionalFormatRules().length, charts: sh.getCharts().length,
      metadata: sh.getDeveloperMetadata().map(m => m.getKey() + '=' + m.getValue())};
    const recv = head.indexOf('received_at');
    if (recv !== -1 && sh.getLastRow() >= 2) {
      const vals = sh.getRange(2, recv + 1, sh.getLastRow() - 1, 1).getValues().map(r => r[0]);
      out.received_at = {dates: vals.filter(v => v instanceof Date).length,
        with_time: vals.filter(v => v instanceof Date && (v.getHours() || v.getMinutes() || v.getSeconds())).length};
    }
    return out;
  });
  return {id_ref: nsOf_(ss.getId()), time_zone: ss.getSpreadsheetTimeZone(), sheets: sheets,
    named_ranges: ss.getNamedRanges().length, flags: flags_(ctx)};
}

function actionSnapshotHash_(ctx) {
  const tabs = ctx.payload.tabs || ctx.ss.getSheets().map(s => s.getName());
  const out = {};
  tabs.forEach(name => {
    const sh = ctx.ss.getSheetByName(name);
    out[name] = sh ? {gid: sh.getSheetId(), content: contentHash_(sh), deep: deepHash_(sh), rows: sh.getLastRow()} : null;
  });
  return {tabs: out, sha256: sha256Hex_(JSON.stringify(out))};
}

function actionCapabilities_(ctx) {
  const checks = {};
  const run = (name, fn) => { try { fn(); checks[name] = 'ok'; } catch (e) { checks[name] = 'error: ' + String(e.message || e).slice(0, 200); } };
  run('open_by_id', () => SpreadsheetApp.openById(ctx.ssId).getName());
  run('developer_metadata', () => ctx.ss.getDeveloperMetadata());
  run('properties', () => { const k = `ns.${ctx.ns}.CAPCHECK`; props_().setProperty(k, '1'); props_().getProperty(k); props_().deleteProperty(k); });
  run('lock', () => { const l = LockService.getDocumentLock(); if (l) { l.tryLock(1000); l.releaseLock(); } });
  run('digest', () => sha256Hex_('x'));
  run('cache', () => CacheService.getScriptCache().put('cf-cap', '1', 60));
  run('active_is_production', () => { if (SpreadsheetApp.getActiveSpreadsheet().getId() !== CF_CONFIG.PRODUCTION_ID) throw new Error('mismatch'); });
  return {checks: checks, ok: Object.values(checks).every(v => v === 'ok')};
}

// شيت حقيقي: هل «=…» بيتحوّل لمعادلة في عمود '@'؟ وحافة الـ grid والـ checkbox والـ dropdown على صفوف جديدة
function actionSelftest_(ctx) {
  if (ctx.production) throw new ApiErr('E_TARGET_NOT_ALLOWED', 'selftest runs on rehearsal copies only');
  const name = `__selftest_${Date.now()}`;
  const sh = ctx.ss.insertSheet(name);
  sh.addDeveloperMetadata(MARKER, `selftest:${name}`);
  const out = {};
  try {
    const samples = ['=1+1', '=HYPERLINK("x")', '+20', '-x', '@n'];
    const col = sh.getRange(1, 1, samples.length, 1);
    col.setNumberFormat('@');
    col.setValues(samples.map(s => [s]));
    const plain = col.getValues().map(r => r[0]), formulas = col.getFormulas().map(r => r[0]);
    out.literal = samples.map((s, i) => plain[i] === s && !formulas[i]);
    const apos = sh.getRange(1, 2, 2, 1);
    apos.setNumberFormat('@');
    apos.setValues([["'=1+1"], ["'=HYPERLINK(\"x\")"]]);
    out.apostrophe = apos.getValues().map(r => r[0]);
    out.apostrophe_formulas = apos.getFormulas().map(r => r[0]);
    out.formula_mode = out.literal.every(Boolean) ? 'literal'
      : (out.apostrophe[0] === '=1+1' && !out.apostrophe_formulas[0]) ? 'apostrophe' : 'unsafe';
    const max = sh.getMaxRows();
    ensureCapacity_(sh, max + 3);
    sh.getRange(max + 1, 1, 3, 1).setValues([['a'], ['b'], ['c']]);
    sh.getRange(max + 1, 3, 3, 1).insertCheckboxes();
    const rule = SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).setAllowInvalid(false).build();
    sh.getRange(max + 1, 4, 3, 1).setDataValidation(rule);
    out.grid = {before: max, after: sh.getMaxRows(), wrote: sh.getRange(max + 3, 1).getValue() === 'c',
      checkbox: !!sh.getRange(max + 3, 3).getDataValidation(), dropdown: !!sh.getRange(max + 3, 4).getDataValidation()};
  } finally {
    if (marked_(sh, `selftest:${name}`)) ctx.ss.deleteSheet(sh);
  }
  return out;
}

// ------------------------------------------------------------------ الترحيل M1–M4

function parseUtcText_(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(String(text || '').trim());
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]));
}

function v1Map_(sh, known) {
  const map = headerMap_(sh);
  const missing = known.filter(h => map[h] === undefined);
  if (missing.length) throw new ApiErr('E_SCHEMA', `${sh.getName()} is not the expected v1 layout (missing ${missing.join(',')})`);
  const extra = map._head.filter(h => h && known.indexOf(h) === -1);
  return {map: map, extra: extra};
}

// تحويل v1 -> v2 لتاب واحد. بيرجّع الصفوف والملاحظات وأحداث الدفتر المتوقعة. نفس الدالة بيستخدمها التحقق.
function transform_(ctx, tab, source, postsText) {
  const known = tab === POSTS ? V1_POSTS : V1_COMMENTS;
  const {map, extra} = v1Map_(source, known);
  const head = (tab === POSTS ? POST_HEAD : COMMENT_HEAD).concat(extra);
  const last = source.getLastRow();
  const values = last >= 2 ? source.getRange(2, 1, last - 1, map._width).getValues() : [];
  const notes = last >= 2 ? source.getRange(2, 1, last - 1, map._width).getNotes() : [];
  const names = ctx.payload.group_names || {};
  const rename = tab === POSTS ? {'post content': 'media_url', permalink: null} : {permalink: 'url', 'post content': 'post_text'};
  const rows = [], rowNotes = [], events = [], malformed = [], unknownStatus = [];
  values.forEach((v, i) => {
    const get = h => map[h] === undefined ? '' : v[map[h]];
    const out = {};
    head.forEach(h => { out[h] = ''; });
    known.forEach(h => { if (head.indexOf(h) !== -1) out[h] = get(h); });
    extra.forEach(h => { out[h] = get(h); });
    const id = idText_(get(tab === POSTS ? 'post_id' : 'comment_id'));
    out[tab === POSTS ? 'post_id' : 'comment_id'] = id;
    out.group_id = idText_(get('group_id'));
    out.post_id = idText_(get('post_id'));
    out.author_id = idText_(get('author_id'));
    out.group_name = names[out.group_id] || '';
    const created = parseUtcText_(get('created_time'));
    if (created) out.created_time = created; else { out.created_time = get('created_time'); if (String(get('created_time')).trim()) malformed.push(id); }
    out.received_at = get('received_at');
    if (tab === POSTS) {
      const pc = String(get('post content') || '');
      out.media_url = pc && pc !== String(get('text') || '') ? pc : '';
      out.url = String(get('url') || get('permalink') || '');
      out.reply = ''; out.reply_status = '';
    } else {
      ['parent_id', 'reply_comment_id', 'reply_target_id'].forEach(h => { out[h] = idText_(get(h)); });
      out.url = String(get('permalink') || '');
      out.is_reply = /^(yes|true|1)$/i.test(String(get('is_reply')).trim());
      out.post_text = postsText[out.post_id] !== undefined ? postsText[out.post_id] : '';
      out.nest_parent_id = ''; out.mentions = '';
      const reply = String(get('reply') || '');
      const legacy = String(get('reply_status') || '').trim();
      let status = LEGACY_STATUS[legacy];
      if (status === undefined) { unknownStatus.push(id); status = legacy; }
      if (reply.trim() && !legacy) status = 'held';
      out.reply = reply; out.reply_status = status;
      if (status === 'sent') events.push({event: 'legacy-sent', kind: 'comment', target_id: id, author_id: out.author_id, note: 'sent before v2; send time unknown', event_id: `legacy-sent:comment:${id}`});
      if (status === 'held') events.push({event: 'held-legacy', kind: 'comment', target_id: id, author_id: out.author_id, note: 'draft predates v2; not authorized', event_id: `held-legacy:comment:${id}`});
      if (status === 'declined' && out.author_id) events.push({event: 'dnc-add', kind: 'comment', target_id: id, author_id: out.author_id, note: 'declined before v2', event_id: `dnc-add:${out.author_id}:comment:${id}`});
    }
    rows.push(head.map(h => out[h]));
    const n = head.map(h => {
      const src = Object.keys(rename).find(k => rename[k] === h) || h;
      return map[src] === undefined ? '' : notes[i][map[src]];
    });
    rowNotes.push(n);
  });
  return {head: head, rows: rows, notes: rowNotes, events: events, malformed: malformed, unknownStatus: unknownStatus};
}

function postsTextMap_(ss, postsSheet, isV2) {
  const map = headerMap_(postsSheet);
  const out = {};
  const last = postsSheet.getLastRow();
  if (last < 2) return out;
  postsSheet.getRange(2, 1, last - 1, map._width).getValues().forEach(v => { out[idText_(v[map.post_id])] = String(v[map.text] || ''); });
  return out;
}

function writeBuild_(sheet, built) {
  const width = built.head.length;
  const total = built.rows.length + 1;
  if (sheet.getMaxRows() < total) sheet.insertRowsAfter(sheet.getMaxRows(), total - sheet.getMaxRows());
  if (sheet.getMaxColumns() < width) sheet.insertColumnsAfter(sheet.getMaxColumns(), width - sheet.getMaxColumns());
  const all = sheet.getRange(1, 1, total, width);
  all.setNumberFormat('@');
  sheet.getRange(1, 1, 1, width).setValues([built.head]);
  if (built.rows.length) {
    sheet.getRange(2, 1, built.rows.length, width).setValues(built.rows.map(r => r.map(safeText_)));
    sheet.getRange(2, 1, built.rows.length, width).setNotes(built.notes);
    ['created_time', 'received_at'].forEach(h => {
      const c = built.head.indexOf(h);
      if (c !== -1) sheet.getRange(2, c + 1, built.rows.length, 1).setNumberFormat('yyyy-mm-dd hh:mm');
    });
  }
}

// التحقق: كل صف وكل خلية في الشيت المبني = التحويل المتوقع من v1 (التواريخ كلحظات UTC)
function compareBuild_(sheet, built) {
  const issues = [];
  const head = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0].map(String);
  if (head.join('|') !== built.head.join('|')) issues.push('header mismatch');
  const rows = sheet.getLastRow() - 1;
  if (rows !== built.rows.length) issues.push(`rows ${rows} != ${built.rows.length}`);
  const n = Math.min(rows, built.rows.length);
  if (n > 0) {
    const vals = sheet.getRange(2, 1, n, built.head.length).getValues();
    const notes = sheet.getRange(2, 1, n, built.head.length).getNotes();
    for (let r = 0; r < n && issues.length < 20; r++) {
      for (let c = 0; c < built.head.length; c++) {
        const want = built.rows[r][c], got = vals[r][c];
        const same = want instanceof Date ? (got instanceof Date && got.getTime() === want.getTime())
          : typeof want === 'boolean' ? (got === want || String(got).toUpperCase() === String(want).toUpperCase())
          : String(got) === String(want);
        if (!same) { issues.push(`row ${r + 2} ${built.head[c]}`); break; }
        if (String(notes[r][c] || '') !== String(built.notes[r][c] || '')) { issues.push(`note row ${r + 2} ${built.head[c]}`); break; }
      }
    }
  }
  return issues;
}

function stepState_(ctx, tab, name) { return nsGet_(ctx.ns, `MIG.${tab}.${name}`) || ''; }
function setStepState_(ctx, tab, name, value) { nsSet_(ctx.ns, `MIG.${tab}.${name}`, value); }

function faultAfter_(ctx, step) {
  if (!ctx.fault || ctx.fault.after_step !== step) return;
  if (ctx.fault.mode === 'throw') throw new ApiErr('E_INTERNAL', `fault injected after ${step}`);
}

function migrateTab_(ctx, run, tab, done) {
  const ss = ctx.ss;
  const orig = ss.getSheetByName(tab);
  if (!orig) throw new ApiErr('E_SCHEMA', `${tab} missing`);
  const gidKey = stepState_(ctx, tab, 'GID');
  if (!gidKey) setStepState_(ctx, tab, 'GID', String(orig.getSheetId()));
  else if (Number(gidKey) !== orig.getSheetId()) throw new ApiErr('E_SCHEMA', `${tab} gid changed`);
  if (stepState_(ctx, tab, 'DONE') === run) return {tab: tab, step: 'done'};
  const scratch = findMarked_(ss, `${run}:build:${tab}`);
  const backup = ss.getSheetByName(`${tab}__v1_backup`);
  if (!stepState_(ctx, tab, 'V1HASH')) setStepState_(ctx, tab, 'V1HASH', contentHash_(orig));
  const v1hash = stepState_(ctx, tab, 'V1HASH');
  const v2hash = stepState_(ctx, tab, 'V2HASH');
  const now = contentHash_(orig);

  if (v2hash && now === v2hash) {
    if (scratch) {                                                   // M4
      if (marked_(scratch, `${run}:build:${tab}`) && contentHash_(scratch) === v2hash) ss.deleteSheet(scratch);
      faultAfter_(ctx, `M4:${tab}`);
      return {tab: tab, step: 'M4'};
    }
    setStepState_(ctx, tab, 'DONE', run);
    return {tab: tab, step: 'done'};
  }
  if (!scratch) {                                                    // M1
    if (now !== v1hash) throw new ApiErr('E_SCHEMA', `${tab} changed since migration start and no build exists`);
    const source = backup && marked_(backup, `${run}:backup:${tab}`) ? backup : orig;
    const built = transform_(ctx, tab, source, tab === COMMENTS ? postsTextMap_(ss, backupOr_(ss, run, POSTS)) : {});
    if (built.unknownStatus.length) throw new ApiErr('E_VALIDATION', `unknown legacy reply_status on ${built.unknownStatus.length} rows`);
    const sh = ss.insertSheet(`__v2_build_${tab}`);
    sh.addDeveloperMetadata(MARKER, `${run}:build:${tab}`);
    writeBuild_(sh, built);
    sh.hideSheet();
    built.events.forEach(e => appendEvent_(ctx, e));
    built.events.forEach(e => {
      if (e.event === 'legacy-sent') mirrorAdd_(ctx, 'SENT', setKey_(e.kind, e.target_id));
      if (e.event === 'held-legacy') mirrorAdd_(ctx, 'HELD', setKey_(e.kind, e.target_id));
      if (e.event === 'dnc-add') mirrorAdd_(ctx, 'DNC', setKey_('author', e.author_id));
    });
    if (ctx.fault && ctx.fault.after_step === `M1:${tab}` && ctx.fault.mode === 'corrupt') {
      sh.getRange(2, 1).setValue('corrupted');
    }
    faultAfter_(ctx, `M1:${tab}`);
    return {tab: tab, step: 'M1', rows: built.rows.length, malformed_dates: built.malformed.length, events: built.events.length};
  }
  if (stepState_(ctx, tab, 'VALID') !== contentHash_(scratch)) {     // تحقق M1
    const source = backup && marked_(backup, `${run}:backup:${tab}`) ? backup : orig;
    if (source === orig && now !== v1hash) throw new ApiErr('E_SCHEMA', `${tab} changed during migration`);
    const built = transform_(ctx, tab, source, tab === COMMENTS ? postsTextMap_(ss, backupOr_(ss, run, POSTS)) : {});
    const issues = compareBuild_(scratch, built);
    if (issues.length) throw new ApiErr('E_VALIDATION', `${tab} build does not match v1: ${issues.slice(0, 5).join('; ')}`, {issues: issues});
    setStepState_(ctx, tab, 'VALID', contentHash_(scratch));
    setStepState_(ctx, tab, 'V2HASH', contentHash_(scratch));
    return {tab: tab, step: 'validated'};
  }
  if (!backup) {                                                     // M2
    if (now !== v1hash) throw new ApiErr('E_SCHEMA', `${tab} changed before backup`);
    const copy = orig.copyTo(ss);
    copy.setName(`${tab}__v1_backup`);
    copy.addDeveloperMetadata(MARKER, `${run}:backup:${tab}`);
    copy.hideSheet();
    if (contentHash_(copy) !== v1hash) throw new ApiErr('E_INTERNAL', `${tab} backup differs from original`);
    faultAfter_(ctx, `M2:${tab}`);
    return {tab: tab, step: 'M2'};
  }
  if (!marked_(backup, `${run}:backup:${tab}`) || contentHash_(backup) !== v1hash) {
    throw new ApiErr('E_SCHEMA', `${tab}__v1_backup is not this run's verified backup`);
  }
  // M3: الأصل (نفس الـ gid) = الشيت المبني. idempotent: لو اتقطع في النص بيتعاد كله
  convergeSheet_(orig, scratch, ctx, `M3:${tab}`);
  if (contentHash_(orig) !== v2hash) throw new ApiErr('E_INTERNAL', `${tab} converge did not match the build`);
  faultAfter_(ctx, `M3:${tab}`);
  return {tab: tab, step: 'M3'};
}

function backupOr_(ss, run, tab) {
  const b = ss.getSheetByName(`${tab}__v1_backup`);
  return b && marked_(b, `${run}:backup:${tab}`) ? b : ss.getSheetByName(tab);
}

function convergeSheet_(target, source, ctx, faultStep) {
  const rows = Math.max(source.getLastRow(), 1), cols = Math.max(source.getLastColumn(), 1);
  if (target.getMaxRows() < rows) target.insertRowsAfter(target.getMaxRows(), rows - target.getMaxRows());
  if (target.getMaxColumns() < cols) target.insertColumnsAfter(target.getMaxColumns(), cols - target.getMaxColumns());
  const old = target.getRange(1, 1, Math.max(target.getLastRow(), 1), Math.max(target.getLastColumn(), 1));
  old.clearContent(); old.clearNote(); old.clearDataValidations(); old.clearFormat();
  if (ctx && ctx.fault && ctx.fault.after_step === faultStep + ':partial') {
    source.getRange(1, 1, Math.min(rows, 10), cols).copyTo(target.getRange(1, 1));
    throw new ApiErr('E_INTERNAL', 'fault injected in the middle of converge');
  }
  source.getRange(1, 1, rows, cols).copyTo(target.getRange(1, 1));
}

function actionMigrateStep_(ctx) {
  const run = String(ctx.payload.run || '');
  if (!/^[\w.-]{6,64}$/.test(run)) throw new ApiErr('E_VALIDATION', 'run label required');
  const current = nsGet_(ctx.ns, 'MIGRATION_RUN');
  if (schemaOf_(ctx.ns) === SCHEMA_V2 && current === run) return {complete: true, schema_version: SCHEMA_V2};
  if (schemaOf_(ctx.ns) === SCHEMA_V2) throw new ApiErr('E_SCHEMA', 'already v2 from another run');
  if (current && current !== run) throw new ApiErr('E_CONFLICT', 'another migration run is in progress');
  if (ctx.production && !inMaintenance_(ctx.ns)) throw new ApiErr('E_MAINTENANCE', 'production must be in maintenance to migrate');
  nsSet_(ctx.ns, 'MIGRATION_RUN', run);
  const steps = [];
  for (const tab of [POSTS, COMMENTS]) {
    let guard = 0;
    while (guard++ < 8) {
      const r = migrateTab_(ctx, run, tab);
      steps.push(r);
      if (r.step === 'done') break;
      if (ctx.overBudget()) return budgetReturn_(ctx, steps.length, {steps: steps});
    }
  }
  nsSet_(ctx.ns, 'SCHEMA_VERSION', SCHEMA_V2);
  return {complete: true, schema_version: SCHEMA_V2, steps: steps};
}

// الرجوع: الأصل (نفس الـ gid) يرجع لمحتوى النسخة __v1_backup، والدفتر والمرايات مابيتمسحوش (القيود دايمة)
function actionRollbackStep_(ctx) {
  const run = String(ctx.payload.run || nsGet_(ctx.ns, 'MIGRATION_RUN') || '');
  if (run && nsGet_(ctx.ns, 'ROLLED_BACK') === run && schemaOf_(ctx.ns) === '1') return {restored: 'already', schema_version: '1'};
  const done = [];
  for (const tab of [POSTS, COMMENTS]) {
    const orig = ctx.ss.getSheetByName(tab);
    const backup = ctx.ss.getSheetByName(`${tab}__v1_backup`);
    if (!backup) { done.push({tab: tab, step: 'no-backup'}); continue; }
    if (!marked_(backup, `${run}:backup:${tab}`)) throw new ApiErr('E_SCHEMA', `${tab} backup is not from run ${run}`);
    // النسخة اتأكدت = v1 لحظة M2، فبصمتها هي المرجع لو مفاتيح الترحيل اتمسحت
    const v1hash = stepState_(ctx, tab, 'V1HASH') || contentHash_(backup);
    if (contentHash_(orig) !== v1hash) {
      convergeSheet_(orig, backup, ctx, `R:${tab}`);
      faultAfter_(ctx, `R:${tab}`);
    }
    if (contentHash_(orig) !== v1hash) throw new ApiErr('E_INTERNAL', `${tab} rollback did not restore v1`);
    const scratch = findMarked_(ctx.ss, `${run}:build:${tab}`);
    if (scratch) scratch.setName(`__v2_failed_${tab}_${Date.now()}`);
    done.push({tab: tab, step: 'restored'});
  }
  nsSet_(ctx.ns, 'SCHEMA_VERSION', '1');
  Object.keys(props_().getProperties()).filter(k => k.indexOf(`ns.${ctx.ns}.MIG.`) === 0).forEach(k => props_().deleteProperty(k));
  nsDel_(ctx.ns, 'MIGRATION_RUN');
  nsSet_(ctx.ns, 'ROLLED_BACK', run);
  return {restored: done, schema_version: '1'};
}

// ------------------------------------------------------------------ التنسيق

function actionFormat_(ctx) {
  [[POSTS, POST_HEAD], [COMMENTS, COMMENT_HEAD]].forEach(([name, head]) => {
    const sh = ctx.ss.getSheetByName(name);
    const map = requireV2Headers_(sh, head);
    const width = map._width, maxRows = sh.getMaxRows(), last = Math.max(sh.getLastRow(), 2);
    const header = sh.getRange(1, 1, 1, width);
    header.setBackground(HEADER_FILL).setFontColor('#FFFFFF').setFontWeight('bold').setFontFamily('Cairo')
      .setFontSize(11).setHorizontalAlignment('center').setVerticalAlignment('middle');
    Object.values(CATEGORY).forEach(cat => cat.cols.forEach(c => {
      if (map[c] !== undefined) sh.getRange(1, map[c] + 1).setBorder(null, null, true, null, null, null, cat.color, SpreadsheetApp.BorderStyle.SOLID_THICK);
    }));
    sh.setFrozenRows(1);
    sh.setFrozenColumns(1);
    const body = sh.getRange(2, 1, maxRows - 1, width);
    body.setFontFamily('Arial').setFontSize(10).setVerticalAlignment('top');
    head.forEach(h => {
      const col = sh.getRange(2, map[h] + 1, maxRows - 1, 1);
      if (h === 'created_time' || h === 'received_at') col.setNumberFormat('yyyy-mm-dd hh:mm').setHorizontalAlignment('center');
      else if (h === 'is_reply' || h === 'reply_status') col.setHorizontalAlignment('center');
      else col.setNumberFormat('@');
      if (WRAP.indexOf(h) !== -1) col.setWrap(true);
    });
    if (map.is_reply !== undefined) sh.getRange(2, map.is_reply + 1, last - 1, 1).insertCheckboxes();
    const rule = SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).setAllowInvalid(false).build();
    sh.getRange(2, map.reply_status + 1, last - 1, 1).setDataValidation(rule);
    const band = SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=ISEVEN(ROW())').setBackground('#F9FAFB')
      .setRanges([sh.getRange(2, 1, maxRows - 1, width)]).build();
    const others = sh.getConditionalFormatRules().filter(r => !r.getBooleanCondition() ||
      String(r.getBooleanCondition().getCriteriaValues()[0]) !== '=ISEVEN(ROW())');
    sh.setConditionalFormatRules(others.concat([band]));
    sh.autoResizeColumns(1, width);
    WRAP.forEach(h => { if (map[h] !== undefined && sh.getColumnWidth(map[h] + 1) > 320) sh.setColumnWidth(map[h] + 1, 320); });
  });
  return {formatted: true};
}

// ------------------------------------------------------------------ الداشبورد

function dropMarked_(ss, prefix) {
  ss.getSheets().filter(s => s.getDeveloperMetadata().some(m => m.getKey() === MARKER && String(m.getValue()).indexOf(prefix) === 0))
    .forEach(s => ss.deleteSheet(s));
}

function activityFormula_(ss) {
  const ps = ss.getSheetByName(POSTS), cs = ss.getSheetByName(COMMENTS);
  const pm = requireV2Headers_(ps, POST_HEAD), cm = requireV2Headers_(cs, COMMENT_HEAD);
  const part = (tab, m, kind, idCol) => {
    const c = h => `${tab}!${colLetter_(m[h])}2:${colLetter_(m[h])}`;
    const id = c(idCol);
    return `IFERROR(FILTER({IF(LEN(${id}),"${kind}",""),${id},${c('group_id')},${c('group_name')}&" · "&${c('group_id')},` +
      `${c('created_time')},INT(${c('created_time')}),WEEKDAY(${c('created_time')},2),TEXT(${c('created_time')},"dddd"),` +
      `INT(${c('received_at')}),IF(${c('reply_status')}="","(none)",${c('reply_status')})},LEN(${id})),{"","","","","","","","","",""})`;
  };
  return `={"kind","id","group_id","group_label","created_at","created_date","weekday_no","weekday","received_date","reply_status";` +
    `${part(POSTS, pm, 'post', 'post_id')};${part(COMMENTS, cm, 'comment', 'comment_id')}}`;
}

const PIVOTS = [
  {title: 'Posts per day (count of post ids)', row: 6, rowCol: 6, kind: 'post', chart: 'COLUMN'},
  {title: 'Comments per day (count of comment ids)', row: 6, rowCol: 6, kind: 'comment', chart: 'COLUMN'},
  {title: 'Activity by weekday (count of rows)', row: 6, rowCol: 7, kind: '', chart: 'COLUMN'},
  {title: 'Top 10 groups by posts (count of post ids)', row: 6, rowCol: 4, kind: 'post', chart: 'BAR', top: 10},
  {title: 'Reply status (rows with a reply)', row: 6, rowCol: 10, kind: '', chart: 'PIE', replyOnly: true}
];

function actionBuildDashboard_(ctx) {
  const ss = ctx.ss;
  dropMarked_(ss, 'dashboard:');
  const act = ss.insertSheet('_activity');
  act.addDeveloperMetadata(MARKER, 'dashboard:activity');
  act.getRange(1, 1).setFormula(activityFormula_(ss));
  act.hideSheet();
  const dash = ss.insertSheet('Dashboard', 0);
  dash.addDeveloperMetadata(MARKER, 'dashboard:main');
  const kpis = [
    ['Posts (all data, not filtered)', `=COUNTA(${POSTS}!A2:A)`],
    ['Comments (all data, not filtered)', `=COUNTA(${COMMENTS}!A2:A)`],
    ['Groups (unique group_id)', `=COUNTUNIQUE(${POSTS}!B2:B)`]
  ];
  const statusRef = (tab, head) => {
    const m = headerMap_(ss.getSheetByName(tab));
    return `${tab}!${colLetter_(m.reply_status)}2:${colLetter_(m.reply_status)}`;
  };
  const pS = statusRef(POSTS, POST_HEAD), cS = statusRef(COMMENTS, COMMENT_HEAD);
  [['Sent (sent + replied)', ['sent', 'replied']], ['Replied', ['replied']], ['Pending', ['pending']], ['Held', ['held']],
    ['Uncertain', ['uncertain']], ['Failed', ['failed']], ['Declined', ['declined']]].forEach(([label, list]) => {
    kpis.push([label, '=' + list.map(s => `COUNTIF(${pS},"${s}")+COUNTIF(${cS},"${s}")`).join('+')]);
  });
  kpis.forEach((k, i) => {
    dash.getRange(1, 1 + i * 2).setValue(k[0]).setFontWeight('bold');
    dash.getRange(2, 1 + i * 2).setFormula(k[1]).setFontSize(18);
  });
  const source = act.getRange('A1:J');
  const slicers = [[9, 'received date'], [8, 'weekday'], [4, 'group']].map(([col, label], i) => {
    const s = dash.insertSlicer(source, 4, 1 + i * 3);
    s.setColumnFilterCriteria(col, null);
    s.setTitle(label);
    return {column: col, label: label};
  });
  const notEmpty = SpreadsheetApp.newFilterCriteria().whenCellNotEmpty().build();
  const pivots = PIVOTS.map((p, i) => {
    const anchor = dash.getRange(30, 1 + i * 4);
    anchor.offset(-1, 0).setValue(p.title).setFontWeight('bold');
    const pt = anchor.createPivotTable(source);
    const group = pt.addRowGroup(p.rowCol);
    pt.addPivotValue(2, SpreadsheetApp.PivotTableSummarizeFunction.COUNTA);
    pt.addFilter(2, notEmpty);
    if (p.kind) pt.addFilter(1, SpreadsheetApp.newFilterCriteria().whenTextEqualTo(p.kind).build());
    if (p.replyOnly) pt.addFilter(10, SpreadsheetApp.newFilterCriteria().whenTextNotEqualTo('(none)').build());
    if (p.top) group.sortBy(pt.getPivotValues()[0], []).sortDescending();
    if (p.rowCol === 7) group.sortAscending();
    const chart = dash.newChart().setChartType(Charts.ChartType[p.chart]).addRange(dash.getRange(30, 1 + i * 4, p.top ? p.top + 1 : 400, 2))
      .setPosition(8, 1 + i * 4, 0, 0).setOption('title', p.title).build();
    dash.insertChart(chart);
    return {title: p.title, anchor: anchor.getA1Notation()};
  });
  return {built: true, kpis: kpis.length, slicers: slicers, pivots: pivots};
}

// تحقق الداشبورد: الأعداد المتوقعة محسوبة من التابات مباشرة (كود منفصل عن معادلات الشيت)
function expectedCounts_(ss, filter) {
  const counts = {posts_by_day: {}, comments_by_day: {}, by_weekday: {}, posts_by_group: {}, by_status: {}};
  const tz = ss.getSpreadsheetTimeZone();
  [[POSTS, POST_HEAD, 'post', 'post_id'], [COMMENTS, COMMENT_HEAD, 'comment', 'comment_id']].forEach(([tab, head, kind, idCol]) => {
    const sh = ss.getSheetByName(tab);
    const m = headerMap_(sh);
    const last = sh.getLastRow();
    if (last < 2) return;
    sh.getRange(2, 1, last - 1, m._width).getValues().forEach(v => {
      if (!idText_(v[m[idCol]])) return;
      const created = v[m.created_time], received = v[m.received_at];
      const day = created instanceof Date ? Utilities.formatDate(created, tz, 'yyyy-MM-dd') : '';
      const rday = received instanceof Date ? Utilities.formatDate(received, tz, 'yyyy-MM-dd') : '';
      const wd = created instanceof Date ? Number(Utilities.formatDate(created, tz, 'u')) : 0;
      const group = String(v[m.group_name] || '') + ' · ' + idText_(v[m.group_id]);
      if (filter && ((filter.group && filter.group !== group) || (filter.weekday && filter.weekday !== wd) || (filter.received && filter.received !== rday))) return;
      const status = String(v[m.reply_status] || '') || '(none)';
      if (kind === 'post') { counts.posts_by_day[day] = (counts.posts_by_day[day] || 0) + 1; counts.posts_by_group[group] = (counts.posts_by_group[group] || 0) + 1; }
      else counts.comments_by_day[day] = (counts.comments_by_day[day] || 0) + 1;
      counts.by_weekday[wd] = (counts.by_weekday[wd] || 0) + 1;
      if (status !== '(none)') counts.by_status[status] = (counts.by_status[status] || 0) + 1;
    });
  });
  return counts;
}

function readPivot_(dash, col) {
  const values = dash.getRange(30, col, 400, 2).getValues();
  const out = {};
  values.forEach(r => {
    const k = r[0];
    if (k === '' || /total/i.test(String(k)) || String(k) === 'المجموع الإجمالي') return;
    const key = k instanceof Date ? Utilities.formatDate(k, dash.getParent().getSpreadsheetTimeZone(), 'yyyy-MM-dd') : String(k);
    out[key] = Number(r[1]);
  });
  return out;
}

function actionCheckDashboard_(ctx) {
  const ss = ctx.ss;
  SpreadsheetApp.flush();
  const dash = ss.getSheetByName('Dashboard');
  if (!dash || !marked_(dash, 'dashboard:main')) throw new ApiErr('E_SCHEMA', 'dashboard missing');
  const compare = (expected) => {
    const got = PIVOTS.map((p, i) => readPivot_(dash, 1 + i * 4));
    const want = [expected.posts_by_day, expected.comments_by_day, expected.by_weekday, expected.posts_by_group, expected.by_status];
    return want.map((w, i) => {
      const g = got[i];
      const keys = Object.keys(w).filter(k => k !== '' && k !== '0');
      const bad = keys.filter(k => Number(g[k] || 0) !== w[k]);
      return {pivot: PIVOTS[i].title, expected_keys: keys.length, mismatches: bad.slice(0, 5)};
    });
  };
  const result = {unfiltered: compare(expectedCounts_(ss, null))};
  result.pivots_ok = result.unfiltered.every(p => !p.mismatches.length);
  result.charts = dash.getCharts().map(c => c.getRanges().map(r => r.getA1Notation()).join(','));
  result.charts_ok = result.charts.length === PIVOTS.length;
  // الـ slicer: نحط فلتر بالكود ونقرا الـ pivots تاني. لو القيم ماتغيرتش خالص والمتوقع اتغير: «مش قابل للقراءة»
  const slicers = dash.getSlicers();
  result.slicers = [];
  try {
    const sample = expectedCounts_(ss, null);
    const group = Object.keys(sample.posts_by_group)[0];
    if (group && slicers.length >= 3) {
      const s = slicers.find(x => x.getColumnPosition() === 4);
      s.setColumnFilterCriteria(4, SpreadsheetApp.newFilterCriteria().whenTextEqualTo(group).build());
      SpreadsheetApp.flush();
      const filtered = compare(expectedCounts_(ss, {group: group}));
      s.setColumnFilterCriteria(4, null);
      SpreadsheetApp.flush();
      result.slicers.push({column: 'group', ok: filtered.every(p => !p.mismatches.length), detail: filtered});
    }
  } catch (err) {
    result.slicers.push({error: String(err.message || err).slice(0, 200)});
  }
  result.slicer_behaviour = result.slicers.length && result.slicers.every(s => s.ok) ? 'verified' : 'unverified';
  return result;
}

// ------------------------------------------------------------------ التحقق بعد الترحيل

function actionVerify_(ctx) {
  const run = String(ctx.payload.run || nsGet_(ctx.ns, 'MIGRATION_RUN') || '');
  const issues = [];
  const counts = {};
  [POSTS, COMMENTS].forEach(tab => {
    const sh = ctx.ss.getSheetByName(tab);
    const backup = ctx.ss.getSheetByName(`${tab}__v1_backup`);
    if (!backup || !marked_(backup, `${run}:backup:${tab}`)) { issues.push(`${tab}: no backup from run`); return; }
    const built = transform_(ctx, tab, backup, tab === COMMENTS ? postsTextMap_(ctx.ss, ctx.ss.getSheetByName(`${POSTS}__v1_backup`)) : {});
    // التنسيق بيتحط بعد الترحيل، فالمقارنة على القيم والملاحظات بس
    compareBuild_(sh, built).forEach(i => issues.push(`${tab}: ${i}`));
    counts[tab] = sh.getLastRow() - 1;
    if (tab === COMMENTS) {
      const m = headerMap_(sh);
      const statuses = sh.getLastRow() >= 2 ? sh.getRange(2, m.reply_status + 1, sh.getLastRow() - 1, 1).getValues().map(r => String(r[0])) : [];
      counts.sent = statuses.filter(s => s === 'sent').length;
      counts.held = statuses.filter(s => s === 'held').length;
      counts.is_reply_true = sh.getLastRow() >= 2 ? sh.getRange(2, m.is_reply + 1, sh.getLastRow() - 1, 1).getValues().filter(r => r[0] === true).length : 0;
    }
    if (tab === POSTS) {
      const m = headerMap_(sh);
      counts.media_url_blank = sh.getLastRow() >= 2 ? sh.getRange(2, m.media_url + 1, sh.getLastRow() - 1, 1).getValues().filter(r => r[0] === '').length : 0;
    }
  });
  const ps = ctx.ss.getSheetByName(POSTS), cs = ctx.ss.getSheetByName(COMMENTS);
  const pids = new Set(Object.keys(rowIndex_(ps, headerMap_(ps), 'post_id')));
  const cm = headerMap_(cs);
  const orphans = cs.getLastRow() >= 2 ? cs.getRange(2, cm.post_id + 1, cs.getLastRow() - 1, 1).getValues().filter(r => !pids.has(idText_(r[0]))).length : 0;
  if (orphans) issues.push(`orphan comments: ${orphans}`);
  if (!verifyChain_(ctx)) issues.push('ledger chain broken');
  const rows = ledgerRows_(ctx);
  ['SENT', 'HELD'].forEach(name => {
    const ev = name === 'SENT' ? 'legacy-sent' : 'held-legacy';
    const fromLedger = new Set(rows.filter(r => r.event === ev).map(r => setKey_(r.kind, r.target_id)));
    const mirror = mirrorLoad_(ctx, name);
    fromLedger.forEach(k => { if (!mirror.has(k)) issues.push(`${name} mirror missing ${k}`); });
  });
  counts.ledger = rows.length;
  return {ok: issues.length === 0, issues: issues.slice(0, 50), counts: counts, schema_version: schemaOf_(ctx.ns)};
}

function actionDeleteEmptyTab_(ctx) {
  const gid = Number(ctx.payload.gid);
  const sh = sheetByGid_(ctx.ss, gid);
  if (!sh) return {deleted: false, reason: 'absent'};
  if ([POSTS, COMMENTS, LEDGER, REQUESTS].indexOf(sh.getName()) !== -1 || /__v1_backup$/.test(sh.getName())) {
    throw new ApiErr('E_VALIDATION', 'refusing to delete a data tab');
  }
  const range = sh.getRange(1, 1, sh.getMaxRows(), sh.getMaxColumns());
  const empty = range.getValues().every(r => r.every(v => v === '')) && range.getNotes().every(r => r.every(v => !v))
    && range.getFormulas().every(r => r.every(v => !v)) && range.getDataValidations().every(r => r.every(v => !v));
  if (!empty) return {deleted: false, reason: 'not empty'};
  ctx.ss.deleteSheet(sh);
  return {deleted: true};
}

function actionRepairCheck_(ctx) {
  const intact = verifyChain_(ctx);
  if (intact) {
    const rows = ledgerRows_(ctx);
    nsSet_(ctx.ns, 'LEDGER_HEAD', rows.length ? `${rows.length}:${rows[rows.length - 1].hash}` : '0:');
  }
  return {intact: intact};
}

const SETUP_ACTIONS = {
  allow_target: {auth: 'admin', target: 'read', lock: true, run: actionAllowTarget_},
  revoke_target: {auth: 'admin', target: 'read', lock: true, run: actionRevokeTarget_},
  set_flags: {auth: 'admin', target: 'write', lock: true, run: actionSetFlags_},
  flags: {auth: 'admin', target: 'read', lock: false, run: flags_},
  namespace_dump: {auth: 'admin', target: 'read', lock: false, run: actionNamespaceDump_},
  inventory: {auth: 'admin', target: 'read', lock: false, run: actionInventory_},
  snapshot_hash: {auth: 'admin', target: 'read', lock: false, run: actionSnapshotHash_},
  capabilities: {auth: 'admin', target: 'write', lock: false, run: actionCapabilities_},
  selftest: {auth: 'admin', target: 'write', lock: true, run: actionSelftest_},
  migrate_step: {auth: 'admin', target: 'write', lock: true, replay: true, run: actionMigrateStep_},
  rollback_step: {auth: 'admin', target: 'write', lock: true, replay: true, run: actionRollbackStep_},
  format: {auth: 'admin', target: 'write', lock: true, replay: true, schema: SCHEMA_V2, run: actionFormat_},
  build_dashboard: {auth: 'admin', target: 'write', lock: true, replay: true, schema: SCHEMA_V2, run: actionBuildDashboard_},
  check_dashboard: {auth: 'admin', target: 'write', lock: true, schema: SCHEMA_V2, run: actionCheckDashboard_},
  verify: {auth: 'admin', target: 'read', lock: true, run: actionVerify_},
  delete_empty_tab: {auth: 'admin', target: 'write', lock: true, run: actionDeleteEmptyTab_},
  repair_check: {auth: 'admin', target: 'write', lock: true, run: actionRepairCheck_}
};
