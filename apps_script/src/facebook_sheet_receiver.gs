/**
 * مستقبل بيانات جروبات فيسبوك -> Google Sheet — API v2 (ENHANCEMENT_PLAN.md §3–§5).
 *
 * كل حاجة بتعدّي على البوابة قبل ما تلمس أي تاب: api_version 2، المفتاح (مفتاح الكتابة أو الأدمن، الهاش بس
 * في zz_config.gs)، والـ schema والصيانة والقفل العام لكل جدول على حدة (namespace = أول 16 حرف من sha256(id)).
 * أي طلب v1 بيترفض من غير ما يقرا أو يكتب حاجة.
 *
 * الملكية: السحب بيكتب أعمدته بس، الخلايا اللي اتغيرت بس، ومابيلمسش reply / reply_status ولا أعمدة صاحب
 * الشيت. received_at بيتكتب أول مرة بس. حالة الرد بتتغيّر من أوامر الأدمن (claim / prepare_submit / mark_status)
 * ومن onEdit بس، وكل تغيير بيتسجّل في دفتر _replies (append-only، بسلسلة hash).
 */
const API_VERSION = 2;
const SCHEMA_V2 = '2';
const POSTS = 'Posts';
const COMMENTS = 'Comments';
const LEDGER = '_replies';
const REQUESTS = '__requests';
const POST_HEAD = ['post_id', 'group_id', 'group_name', 'author', 'author_id', 'text', 'media_url',
  'created_time', 'received_at', 'url', 'comment_sort', 'reply', 'reply_status'];
const COMMENT_HEAD = ['comment_id', 'post_id', 'parent_id', 'author', 'author_id', 'text', 'created_time',
  'received_at', 'group_id', 'group_name', 'reply_comment_id', 'is_reply', 'reply_target_id', 'author_url', 'url',
  'nest_parent_id', 'mentions', 'reply', 'reply_status', 'post_text'];
const REPLY_COLS = ['reply', 'reply_status'];
const STATUSES = ['pending', 'sending', 'sent', 'uncertain', 'replied', 'declined', 'failed', 'held'];
const LEDGER_HEAD = ['event_id', 'ts', 'event', 'kind', 'target_id', 'post_id', 'group_id', 'author_id',
  'claim_id', 'fence', 'account_id', 'reply_sha256', 'sent_reply_id', 'sent_at', 'status_from', 'status_to',
  'note', 'prev_hash', 'hash'];
const LEASE_MS = 5 * 60 * 1000;
const SUBMIT_WINDOW_MS = 30 * 1000;
const BUDGET_MAX_MS = 240000;
const SHARD_BYTES = 8000;
const MIRROR_BUDGET = 350 * 1024;
const LIMITS = {posts: 25, comments: 500, body: 2 * 1024 * 1024, text: 50000};
const ID_RE = /^\d{3,25}$/;
const POST_FIELDS = ['post_id', 'group_id', 'group_name', 'author', 'author_id', 'text', 'media_url', 'created_time',
  'url', 'comment_sort', 'comments'];
const COMMENT_FIELDS = ['comment_id', 'parent_id', 'author', 'author_id', 'text', 'created_time', 'reply_comment_id',
  'is_reply', 'reply_target_id', 'author_url', 'url', 'nest_parent_id', 'mentions', 'moderation'];

// ------------------------------------------------------------------ أدوات عامة

class ApiErr extends Error {
  constructor(code, message, extra) { super(message); this.code = code; this.extra = extra || {}; }
}
const RETRYABLE = {E_LOCK_BUSY: true, E_INTERNAL: true, E_BUDGET: true};

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function sha256Hex_(text) {
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(text), Utilities.Charset.UTF_8);
  return bytes.map(b => ((b + 256) % 256).toString(16).padStart(2, '0')).join('');
}

function safeEqual_(a, b) {
  a = String(a); b = String(b);
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function idText_(value) {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'number') return value.toLocaleString('en-US', {useGrouping: false, maximumFractionDigits: 0});
  return String(value).replace(/^'/, '').trim();
}

function nowIso_() { return new Date().toISOString(); }

function props_() { return PropertiesService.getScriptProperties(); }

function nsOf_(ssId) { return sha256Hex_(ssId).slice(0, 16); }

function nsGet_(ns, name) { return props_().getProperty(`ns.${ns}.${name}`); }
function nsSet_(ns, name, value) { props_().setProperty(`ns.${ns}.${name}`, String(value)); }
function nsDel_(ns, name) { props_().deleteProperty(`ns.${ns}.${name}`); }
function globalGet_(name) { return props_().getProperty(`global.${name}`); }
function globalSet_(name, value) { props_().setProperty(`global.${name}`, String(value)); }

function schemaOf_(ns) { return nsGet_(ns, 'SCHEMA_VERSION') || '1'; }
function inMaintenance_(ns) { return nsGet_(ns, 'MAINTENANCE') === 'true'; }
function repliesKilled_() { return globalGet_('REPLIES_KILL') === 'true'; }

// ------------------------------------------------------------------ مداخل الـ web app

function doGet(e) {
  const params = (e && e.parameter) || {};
  if (params.action && params.action !== 'ping') {
    return json_({ok: false, api_version: API_VERSION, code: 'E_VERSION',
      error: 'api_version 2 required: use POST with the v2 envelope'});
  }
  const ns = nsOf_(CF_CONFIG.PRODUCTION_ID);
  return json_({ok: true, api_version: API_VERSION, schema_version: schemaOf_(ns), maintenance: inMaintenance_(ns),
    replies_killed: repliesKilled_(), config_version: CF_CONFIG.CONFIG_VERSION});
}

function doPost(e) {
  const started = Date.now();
  let req = {};
  try {
    const raw = (e && e.postData && e.postData.contents) || '';
    if (raw.length > LIMITS.body) throw new ApiErr('E_VALIDATION', 'body too large');
    try { req = JSON.parse(raw || '{}'); } catch (err) { throw new ApiErr('E_VERSION', 'api_version 2 required'); }
    if (req.api_version !== API_VERSION) throw new ApiErr('E_VERSION', 'api_version 2 required');
    // الأوامر في ملفين (sheet_setup.gs للأدمن)، والبحث وقت النداء فترتيب تحميل الملفات مايفرقش
    const action = ACTIONS[req.action] || (typeof SETUP_ACTIONS !== 'undefined' ? SETUP_ACTIONS[req.action] : undefined);
    if (!action || !Object.prototype.hasOwnProperty.call(ACTIONS[req.action] ? ACTIONS : SETUP_ACTIONS, req.action)) {
      throw new ApiErr('E_VALIDATION', 'unknown action');
    }
    authorize_(req, action);
    const ctx = context_(req, action, started);
    const result = action.lock ? withLock_(() => replayable_(ctx, action)) : action.run(ctx);
    return json_(Object.assign({ok: true, api_version: API_VERSION, request_id: req.request_id || null,
      replayed: false, warnings: ctx.warnings}, result && result.__envelope ? result.__envelope : {result: result}));
  } catch (err) {
    const code = err instanceof ApiErr ? err.code : 'E_INTERNAL';
    return json_(Object.assign({ok: false, api_version: API_VERSION, request_id: req.request_id || null, code: code,
      retryable: !!RETRYABLE[code], error: String(err && err.message || err)}, err.extra || {}));
  }
}

function authorize_(req, action) {
  const auth = req.auth || {};
  const key = String(auth.key || '');
  if (!key) throw new ApiErr('E_AUTH', 'missing key');
  const hash = sha256Hex_(key);
  const kind = safeEqual_(hash, CF_CONFIG.ADMIN_HASH) ? 'admin' : safeEqual_(hash, CF_CONFIG.INGEST_HASH) ? 'ingest' : '';
  if (!kind) throw new ApiErr('E_AUTH', 'bad key');
  if (action.auth === 'admin' && kind !== 'admin') throw new ApiErr('E_AUTH', 'admin key required');
  req.authKind = kind;
}

// الهدف: السحب دايمًا على الجدول الإنتاجي (مفيش target). أوامر الأدمن لازم target صريح: الإنتاجي أو جدول
// تجربة مسموح بيه (قايمة السماح بتنتهي بعد 24 ساعة، و CF-backup- قراءة بس).
function context_(req, action, started) {
  const budget = Math.min(Number(req.budget_ms) || BUDGET_MAX_MS, BUDGET_MAX_MS);
  const ctx = {req: req, payload: req.payload || {}, cursor: req.cursor === undefined ? null : req.cursor,
    started: started, budget: budget, warnings: [], elapsed: () => Date.now() - started};
  ctx.overBudget = () => ctx.elapsed() > budget;
  if (action.target === 'none') {
    if (req.target !== undefined) throw new ApiErr('E_VALIDATION', 'target not accepted for this action');
    ctx.ss = productionSpreadsheet_();
  } else {
    if (!req.target) throw new ApiErr('E_VALIDATION', 'explicit target required');
    ctx.ss = resolveTarget_(String(req.target), action.target === 'read');
  }
  ctx.ssId = ctx.ss.getId();
  ctx.production = ctx.ssId === CF_CONFIG.PRODUCTION_ID;
  ctx.ns = nsOf_(ctx.ssId);
  if (ctx.payload.fault !== undefined) {
    const entry = allowlist_()[ctx.ssId];
    if (ctx.production || !entry || entry.mode !== 'rw') throw new ApiErr('E_VALIDATION', 'fault injection not allowed here');
    ctx.fault = ctx.payload.fault;
  }
  if (action.schema && schemaOf_(ctx.ns) !== action.schema) throw new ApiErr('E_SCHEMA', `schema ${schemaOf_(ctx.ns)} != ${action.schema}`);
  if (action.maintenance && inMaintenance_(ctx.ns)) throw new ApiErr('E_MAINTENANCE', 'maintenance');
  if (action.replies && repliesKilled_()) throw new ApiErr('E_KILLED', 'reply actions disabled');
  return ctx;
}

function productionSpreadsheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss || ss.getId() !== CF_CONFIG.PRODUCTION_ID) throw new ApiErr('E_TARGET_NOT_ALLOWED', 'bound spreadsheet is not production');
  return ss;
}

function allowlist_() {
  try { return JSON.parse(globalGet_('REHEARSAL_ALLOWLIST') || '{}'); } catch (e) { return {}; }
}

function resolveTarget_(id, readOnly) {
  if (id === CF_CONFIG.PRODUCTION_ID) return productionSpreadsheet_();
  const entry = allowlist_()[id];
  if (!entry || entry.expires < Date.now()) throw new ApiErr('E_TARGET_NOT_ALLOWED', 'target not allowlisted');
  if (entry.mode !== 'rw' && !readOnly) throw new ApiErr('E_TARGET_NOT_ALLOWED', 'target is read-only');
  return SpreadsheetApp.openById(id);
}

function withLock_(fn) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new ApiErr('E_LOCK_BUSY', 'script lock busy');
  try { return fn(); } finally { lock.releaseLock(); }
}

// نفس request_id = نفس النتيجة، من غير ما الأمر يتنفّذ تاني (طلب اتنفّذ ورده ضاع)
function replayable_(ctx, action) {
  const id = String(ctx.req.request_id || '');
  if (!id || !action.replay) return action.run(ctx);
  const sh = ctx.ss.getSheetByName(REQUESTS);
  if (sh && sh.getLastRow() >= 2) {
    const ids = sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues();
    for (let i = ids.length - 1; i >= 0; i--) {
      if (String(ids[i][0]) === id) {
        const stored = JSON.parse(String(sh.getRange(i + 2, 4).getValue() || '{}'));
        return {__envelope: {result: stored, replayed: true}};
      }
    }
  }
  const result = action.run(ctx);
  if (result && result.__envelope && result.__envelope.code === 'E_BUDGET') return result;
  let text = JSON.stringify(result === undefined ? null : result);
  if (text.length > 40000) text = JSON.stringify({summary: 'large result', sha256: sha256Hex_(text)});
  const store = hiddenSheet_(ctx.ss, REQUESTS, ['request_id', 'action', 'created_at', 'result']);
  ensureCapacity_(store, store.getLastRow() + 1);
  store.getRange(store.getLastRow() + 1, 1, 1, 4).setValues([[id, ctx.req.action, nowIso_(), text]]);
  if (ctx.fault && ctx.fault.mode === 'drop_response' && ctx.fault.after_step === ctx.req.action) {
    throw new ApiErr('E_FAULT', 'fault: response dropped after success');
  }
  return result;
}

function budgetReturn_(ctx, cursor, partial) {
  return {__envelope: {ok: false, code: 'E_BUDGET', retryable: true, cursor: cursor, result: partial || null}};
}

// ------------------------------------------------------------------ تابات وأعمدة

function hiddenSheet_(ss, name, head) {
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, head.length).setValues([head]);
    sh.setFrozenRows(1);
    sh.hideSheet();
  }
  return sh;
}

function headerMap_(sh) {
  const width = Math.max(sh.getLastColumn(), 1);
  const head = sh.getRange(1, 1, 1, width).getValues()[0];
  const map = {};
  head.forEach((name, i) => { if (name !== '' && map[String(name)] === undefined) map[String(name)] = i; });
  map._width = head.length;
  map._head = head.map(String);
  return map;
}

function requireV2Headers_(sh, head) {
  const map = headerMap_(sh);
  const missing = head.filter(h => map[h] === undefined);
  if (missing.length) throw new ApiErr('E_SCHEMA', `${sh.getName()} missing v2 columns: ${missing.join(',')}`);
  return map;
}

// صفوف بتتضاف تحت خلية checkbox بتورث الـ checkbox وقيمتها FALSE (اتشاف في تجربة P4 على شيت حقيقي) — وده بيخلّي
// getLastRow يقفز لآخر الـ grid والإضافة الجاية تنزل بعد مئات الصفوف الفاضية. فبنمسح الموروث فورًا.
function ensureCapacity_(sh, lastRowNeeded) {
  const max = sh.getMaxRows();
  if (lastRowNeeded <= max) return;
  const add = lastRowNeeded - max + 500;
  sh.insertRowsAfter(max, add);
  const fresh = sh.getRange(max + 1, 1, add, sh.getMaxColumns());
  fresh.clearDataValidations();
  fresh.clearContent();
}

// booleans في خلية '@' بتتحفظ نص «TRUE» (شيت حقيقي): عمود is_reply لازم يبقى General قبل الكتابة
function prepareTypedColumns_(sh, map, first, count) {
  if (map.is_reply !== undefined) sh.getRange(first, map.is_reply + 1, count, 1).setNumberFormat('General');
  ['created_time', 'received_at'].forEach(h => {
    if (map[h] !== undefined) sh.getRange(first, map[h] + 1, count, 1).setNumberFormat('yyyy-mm-dd hh:mm');
  });
}

function colLetter_(index0) {
  let n = index0 + 1, s = '';
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

// نص بيبدأ بـ «=» مايتحوّلش لمعادلة: الطريقة بتتحدد في selftest (FORMULA_MODE) على شيت حقيقي
function safeText_(value) {
  if (typeof value !== 'string' || value.charAt(0) !== '=') return value;
  return (globalGet_('FORMULA_MODE') || 'apostrophe') === 'apostrophe' ? "'" + value : value;
}

function rowIndex_(sh, map, idCol) {
  const index = {};
  const last = sh.getLastRow();
  if (last < 2) return index;
  sh.getRange(2, map[idCol] + 1, last - 1, 1).getValues().forEach((r, i) => {
    const key = idText_(r[0]);
    if (key && !index[key]) index[key] = i + 2;
  });
  return index;
}

// ------------------------------------------------------------------ التحقق من بيانات السحب

function parseInstant_(value) {
  if (value === '' || value === null || value === undefined) return '';
  const text = String(value);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$/.test(text)) {
    throw new ApiErr('E_VALIDATION', `created_time must be ISO 8601 with offset: ${text.slice(0, 40)}`);
  }
  const d = new Date(text);
  if (isNaN(d.getTime())) throw new ApiErr('E_VALIDATION', 'bad created_time');
  return d;
}

function checkId_(value, field, optional) {
  const id = idText_(value);
  if (!id && optional) return '';
  if (!ID_RE.test(id)) throw new ApiErr('E_VALIDATION', `${field} is not a numeric id`);
  return id;
}

function checkText_(value, field) {
  if (value === undefined || value === null) return '';
  if (typeof value !== 'string') throw new ApiErr('E_VALIDATION', `${field} must be text`);
  if (value.length > LIMITS.text) throw new ApiErr('E_VALIDATION', `${field} too long`);
  return value;
}

function checkKeys_(obj, allowed, where) {
  Object.keys(obj).forEach(k => {
    if (allowed.indexOf(k) === -1) throw new ApiErr('E_VALIDATION', `${where}: field not accepted: ${k}`);
  });
}

function checkPostUrl_(url, groupId, postId, field) {
  const text = checkText_(url, field);
  if (!text) return '';
  const m = /^https:\/\/www\.facebook\.com\/groups\/([^/?#]+)\/posts\/(\d+)\/?(\?.*)?$/.exec(text);
  if (!m || (m[1] !== groupId && !/\D/.test(m[1]) ) || m[2] !== postId) {
    throw new ApiErr('E_VALIDATION', `${field} does not match its group/post ids`);
  }
  return text;
}

function validateIngest_(payload) {
  checkKeys_(payload, ['group_id', 'posts', 'account_id', 'fault'], 'payload');
  const groupId = checkId_(payload.group_id, 'group_id');
  const posts = payload.posts || [];
  if (!Array.isArray(posts) || posts.length > LIMITS.posts) throw new ApiErr('E_VALIDATION', 'too many posts');
  let comments = 0;
  const out = posts.map(p => {
    checkKeys_(p, POST_FIELDS, 'post');
    const postId = checkId_(p.post_id, 'post_id');
    if (p.group_id !== undefined && idText_(p.group_id) !== groupId) throw new ApiErr('E_VALIDATION', 'post group_id mismatch');
    const cs = p.comments || [];
    comments += cs.length;
    return {
      post_id: postId, group_id: groupId, group_name: checkText_(p.group_name, 'group_name'),
      author: checkText_(p.author, 'author'), author_id: checkId_(p.author_id, 'author_id', true),
      text: checkText_(p.text, 'text'), media_url: checkText_(p.media_url, 'media_url'),
      created_time: parseInstant_(p.created_time), url: checkPostUrl_(p.url, groupId, postId, 'url'),
      comment_sort: checkText_(p.comment_sort, 'comment_sort'),
      comments: cs.map(c => {
        checkKeys_(c, COMMENT_FIELDS, 'comment');
        const id = checkId_(c.comment_id, 'comment_id');
        const url = checkText_(c.url, 'comment url');
        if (url && !(url.indexOf(`/posts/${postId}/`) > 0 && url.indexOf(id) > 0)) throw new ApiErr('E_VALIDATION', 'comment url mismatch');
        if (c.is_reply !== undefined && typeof c.is_reply !== 'boolean') throw new ApiErr('E_VALIDATION', 'is_reply must be boolean');
        const mentions = Array.isArray(c.mentions) ? c.mentions.map(m => checkId_(m, 'mention')).join(',') : checkText_(c.mentions, 'mentions');
        return {
          comment_id: id, parent_id: checkId_(c.parent_id, 'parent_id', true), author: checkText_(c.author, 'author'),
          author_id: checkId_(c.author_id, 'author_id', true), text: checkText_(c.text, 'text'),
          created_time: parseInstant_(c.created_time), reply_comment_id: checkId_(c.reply_comment_id, 'reply_comment_id', true),
          is_reply: !!c.is_reply, reply_target_id: checkId_(c.reply_target_id, 'reply_target_id', true),
          author_url: checkText_(c.author_url, 'author_url'), url: url,
          nest_parent_id: checkId_(c.nest_parent_id, 'nest_parent_id', true), mentions: mentions,
          moderation: checkText_(c.moderation, 'moderation')
        };
      })
    };
  });
  if (comments > LIMITS.comments) throw new ApiErr('E_VALIDATION', 'too many comments');
  return {groupId: groupId, posts: out, accountId: checkId_(payload.account_id, 'account_id', true)};
}

// ------------------------------------------------------------------ الكتابة (upsert بالخلايا اللي اتغيرت بس)

const FROZEN_AFTER_SEND = ['author_id', 'post_id', 'group_id', 'parent_id', 'reply_comment_id', 'url'];

function upsertRows_(ctx, sh, head, idCol, items, kind) {
  const map = requireV2Headers_(sh, head);
  const index = rowIndex_(sh, map, idCol);
  const owned = head.filter(h => REPLY_COLS.indexOf(h) === -1);
  const fresh = [];
  let updated = 0, conflicts = 0;
  const now = new Date();
  items.forEach(item => {
    const hit = index[item.id];
    if (!hit) {
      const row = new Array(map._width).fill('');
      owned.forEach(h => { if (item.values[h] !== undefined) row[map[h]] = safeText_(item.values[h]); });
      row[map.received_at] = now;
      fresh.push(row);
      return;
    }
    const current = sh.getRange(hit, 1, 1, map._width).getValues()[0];
    const frozen = targetHasAttempt_(ctx, kind, item.id);
    const changes = [];
    owned.forEach(h => {
      if (h === 'received_at' || item.values[h] === undefined) return;
      const next = safeText_(item.values[h]);
      const cur = current[map[h]];
      const same = (next instanceof Date && cur instanceof Date) ? next.getTime() === cur.getTime() : String(next) === String(cur);
      if (same) return;
      if (frozen && FROZEN_AFTER_SEND.indexOf(h) !== -1 && String(cur) !== '') {
        conflicts++;
        appendEvent_(ctx, {event: 'conflict', kind: kind, target_id: item.id, note: `ingest tried to change ${h}`});
        return;
      }
      changes.push([map[h], next]);
    });
    writeRuns_(sh, hit, changes);
    if (changes.length) updated++;
  });
  if (fresh.length) {
    const first = sh.getLastRow() + 1;
    ensureCapacity_(sh, first + fresh.length - 1);
    const range = sh.getRange(first, 1, fresh.length, map._width);
    range.setNumberFormat('@');
    prepareTypedColumns_(sh, map, first, fresh.length);
    range.setValues(fresh);
    applyRowFormats_(sh, map, head, first, fresh.length);
  }
  return {new: fresh.length, updated: updated, conflicts: conflicts};
}

// خلايا متجاورة في طلب واحد؛ عمود صاحب الشيت في النص بيقطع المجموعة فمابيتكتبش
function writeRuns_(sh, row, changes) {
  changes.sort((a, b) => a[0] - b[0]);
  let i = 0;
  while (i < changes.length) {
    let j = i;
    while (j + 1 < changes.length && changes[j + 1][0] === changes[j][0] + 1) j++;
    sh.getRange(row, changes[i][0] + 1, 1, j - i + 1).setValues([changes.slice(i, j + 1).map(c => c[1])]);
    i = j + 1;
  }
}

function applyRowFormats_(sh, map, head, first, count) {
  ['created_time', 'received_at'].forEach(h => {
    if (map[h] !== undefined) sh.getRange(first, map[h] + 1, count, 1).setNumberFormat('yyyy-mm-dd hh:mm');
  });
  if (map.is_reply !== undefined) sh.getRange(first, map.is_reply + 1, count, 1).insertCheckboxes();
  if (map.reply_status !== undefined) {
    const rule = SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).setAllowInvalid(false).build();
    sh.getRange(first, map.reply_status + 1, count, 1).setDataValidation(rule);
  }
}

function actionIngest_(ctx) {
  const data = validateIngest_(ctx.payload);
  const ps = ctx.ss.getSheetByName(POSTS), cs = ctx.ss.getSheetByName(COMMENTS);
  if (!ps || !cs) throw new ApiErr('E_SCHEMA', 'tabs missing');
  const postItems = [], commentItems = [];
  const seenP = {}, seenC = {};
  const postText = {};
  data.posts.forEach(p => {
    postText[p.post_id] = p.text;
    if (!seenP[p.post_id]) {
      seenP[p.post_id] = true;
      postItems.push({id: p.post_id, values: {post_id: p.post_id, group_id: p.group_id, group_name: p.group_name,
        author: p.author, author_id: p.author_id, text: p.text, media_url: p.media_url, created_time: p.created_time,
        url: p.url, comment_sort: p.comment_sort}});
    }
    p.comments.forEach(c => {
      if (seenC[c.comment_id]) return;
      seenC[c.comment_id] = true;
      commentItems.push({id: c.comment_id, raw: c, values: {comment_id: c.comment_id, post_id: p.post_id,
        parent_id: c.parent_id, author: c.author, author_id: c.author_id, text: c.text, created_time: c.created_time,
        group_id: p.group_id, group_name: p.group_name, reply_comment_id: c.reply_comment_id, is_reply: c.is_reply,
        reply_target_id: c.reply_target_id || c.comment_id, author_url: c.author_url, url: c.url,
        nest_parent_id: c.nest_parent_id, mentions: c.mentions, post_text: p.text}});
    });
  });
  const posts = upsertRows_(ctx, ps, POST_HEAD, 'post_id', postItems, 'post');
  const comments = upsertRows_(ctx, cs, COMMENT_HEAD, 'comment_id', commentItems, 'comment');
  const outcome = detectOutcomes_(ctx, commentItems.map(i => i.raw), data);
  return {posts_new: posts.new, posts_updated: posts.updated, comments_new: comments.new,
    comments_updated: comments.updated, conflicts: posts.conflicts + comments.conflicts, outcomes: outcome};
}

// ------------------------------------------------------------------ الدفتر (_replies) والمرايات

function ledgerSheet_(ss) { return hiddenSheet_(ss, LEDGER, LEDGER_HEAD); }

function ledgerRows_(ctx) {
  if (ctx._ledger) return ctx._ledger;
  const sh = ctx.ss.getSheetByName(LEDGER);
  if (!sh || sh.getLastRow() < 2) return (ctx._ledger = []);
  const values = sh.getRange(2, 1, sh.getLastRow() - 1, LEDGER_HEAD.length).getValues();
  return (ctx._ledger = values.map(v => Object.fromEntries(LEDGER_HEAD.map((h, i) => [h, String(v[i])]))));
}

function eventHash_(prev, e) {
  return sha256Hex_(prev + '|' + LEDGER_HEAD.slice(0, -2).map(h => String(e[h] === undefined ? '' : e[h])).join('␟'));
}

// الحدث بيتسجّل الأول، وبعدين المراية. event_id ثابت فالإعادة مابتكرّرش.
function appendEvent_(ctx, e) {
  e.event_id = e.event_id || `${ctx.req.request_id || 'x'}:${e.event}:${e.kind || ''}:${e.target_id || e.author_id || ''}`;
  const rows = ledgerRows_(ctx);
  if (rows.some(r => r.event_id === e.event_id)) return false;
  checkLedgerHead_(ctx);
  const sh = ledgerSheet_(ctx.ss);
  e.ts = e.ts || nowIso_();
  const prev = rows.length ? rows[rows.length - 1].hash : '';
  e.prev_hash = prev;
  e.hash = eventHash_(prev, e);
  const row = LEDGER_HEAD.map(h => e[h] === undefined ? '' : String(e[h]));
  ensureCapacity_(sh, sh.getLastRow() + 1);
  const at = sh.getLastRow() + 1;
  sh.getRange(at, 1, 1, row.length).setNumberFormat('@');
  sh.getRange(at, 1, 1, row.length).setValues([row]);
  rows.push(Object.fromEntries(LEDGER_HEAD.map((h, i) => [h, row[i]])));
  nsSet_(ctx.ns, 'LEDGER_HEAD', `${rows.length}:${e.hash}`);
  return true;
}

// حد عدّل أو مسح صفوف من الدفتر = الردود كلها تقف لحد repair_check
function checkLedgerHead_(ctx) {
  const head = nsGet_(ctx.ns, 'LEDGER_HEAD');
  const rows = ledgerRows_(ctx);
  if (!head) {
    if (rows.length) throw integrityFail_(ctx, 'ledger has rows but no recorded head');
    return;
  }
  const [count, hash] = head.split(':');
  if (Number(count) !== rows.length || (rows.length && rows[rows.length - 1].hash !== hash)) {
    throw integrityFail_(ctx, 'ledger head mismatch');
  }
}

function integrityFail_(ctx, why) {
  globalSet_('REPLIES_KILL', 'true');
  return new ApiErr('E_INTEGRITY', why);
}

function verifyChain_(ctx) {
  let prev = '';
  for (const r of ledgerRows_(ctx)) {
    if (r.prev_hash !== prev || eventHash_(prev, r) !== r.hash) return false;
    prev = r.hash;
  }
  return true;
}

// مرايات مقسّمة في Script Properties (كل جزء ≤ 8000 بايت). base36 للـ ids عشان الحجم.
function encId_(id) { return BigInt(id).toString(36); }
function setKey_(kind, id) { return (kind === 'post' ? 'p' : kind === 'comment' ? 'c' : 'a') + encId_(id); }

function mirrorLoad_(ctx, name) {
  ctx._mirrors = ctx._mirrors || {};
  if (ctx._mirrors[name]) return ctx._mirrors[name];
  const n = Number(nsGet_(ctx.ns, `SET.${name}.n`) || 0);
  const set = new Set();
  for (let i = 0; i < n; i++) (nsGet_(ctx.ns, `SET.${name}.${i}`) || '').split(',').filter(Boolean).forEach(x => set.add(x));
  return (ctx._mirrors[name] = set);
}

function mirrorAdd_(ctx, name, key) {
  const set = mirrorLoad_(ctx, name);
  if (set.has(key)) return;
  checkCapacity_(ctx, key.length + 1);
  let n = Number(nsGet_(ctx.ns, `SET.${name}.n`) || 0);
  let last = n ? (nsGet_(ctx.ns, `SET.${name}.${n - 1}`) || '') : '';
  if (!n || last.length + key.length + 1 > SHARD_BYTES) {
    nsSet_(ctx.ns, `SET.${name}.${n}`, key);
    nsSet_(ctx.ns, `SET.${name}.n`, n + 1);
  } else {
    nsSet_(ctx.ns, `SET.${name}.${n - 1}`, last ? last + ',' + key : key);
  }
  set.add(key);
}

function mirrorBytes_(ns) {
  const all = props_().getProperties();
  return Object.keys(all).filter(k => k.indexOf(`ns.${ns}.SET.`) === 0).reduce((s, k) => s + k.length + all[k].length, 0);
}

function checkCapacity_(ctx, adding) {
  const used = mirrorBytes_(ctx.ns) + adding;
  if (used >= MIRROR_BUDGET * 0.7 && ctx.warnings.indexOf('capacity_warning') === -1) ctx.warnings.push('capacity_warning');
  return used;
}

function capacityBlocksClaims_(ctx) { return mirrorBytes_(ctx.ns) >= MIRROR_BUDGET * 0.9; }

// القيود: اتحاد الدفتر والمرايات. أي مصدر بيقول ممنوع = ممنوع، والناقص بيتصلّح.
function restricted_(ctx, name, key, ledgerTest) {
  const inMirror = mirrorLoad_(ctx, name).has(key);
  const inLedger = ledgerRows_(ctx).some(ledgerTest);
  if (inLedger && !inMirror) mirrorAdd_(ctx, name, key);
  return inMirror || inLedger;
}

function isSent_(ctx, kind, id) {
  return restricted_(ctx, 'SENT', setKey_(kind, id), r => r.kind === kind && r.target_id === id &&
    (r.event === 'legacy-sent' || r.status_to === 'sent' || r.status_to === 'replied'));
}
function isHeld_(ctx, kind, id) {
  return restricted_(ctx, 'HELD', setKey_(kind, id), r => r.kind === kind && r.target_id === id && r.event === 'held-legacy');
}
function isUncertain_(ctx, kind, id) {
  return restricted_(ctx, 'UNCERT', setKey_(kind, id), r => r.kind === kind && r.target_id === id && r.status_to === 'uncertain');
}
function dncActive_(ctx, authorId) {
  if (!authorId) return false;
  // الحالة = طيّ أحداث الشخص بالترتيب (إضافة/شيل مصرّح)، والمراية بتقول «ممنوع» لو أي إضافة حصلت
  let active = false;
  ledgerRows_(ctx).forEach(r => {
    if (r.author_id !== authorId) return;
    if (r.event === 'dnc-add') active = true;
    if (r.event === 'dnc-remove') active = false;
  });
  const key = setKey_('author', authorId);
  if (active && !mirrorLoad_(ctx, 'DNC').has(key)) mirrorAdd_(ctx, 'DNC', key);
  return active || (mirrorLoad_(ctx, 'DNC').has(key) && !ledgerRows_(ctx).some(r => r.author_id === authorId && r.event === 'dnc-remove'));
}
function targetHasAttempt_(ctx, kind, id) {
  if (!ctx.ss.getSheetByName(LEDGER) && !nsGet_(ctx.ns, 'SET.SENT.n')) return false;
  return isSent_(ctx, kind, id) || isUncertain_(ctx, kind, id) ||
    ledgerRows_(ctx).some(r => r.kind === kind && r.target_id === id && r.event === 'submit_authorized');
}

function addDnc_(ctx, authorId, source, reason) {
  if (!authorId) return false;
  appendEvent_(ctx, {event: 'dnc-add', kind: source.kind, target_id: source.id, author_id: authorId, note: reason,
    event_id: `dnc-add:${authorId}:${source.kind}:${source.id}`});
  mirrorAdd_(ctx, 'DNC', setKey_('author', authorId));
  return true;
}

// ------------------------------------------------------------------ صفوف الردود

function tabFor_(kind) { return kind === 'post' ? POSTS : kind === 'comment' ? COMMENTS : null; }

function findTarget_(ctx, kind, id) {
  const name = tabFor_(kind);
  if (!name) throw new ApiErr('E_VALIDATION', 'kind must be post or comment');
  const sh = ctx.ss.getSheetByName(name);
  const map = requireV2Headers_(sh, kind === 'post' ? POST_HEAD : COMMENT_HEAD);
  const row = rowIndex_(sh, map, kind === 'post' ? 'post_id' : 'comment_id')[id];
  if (!row) throw new ApiErr('E_NOT_ELIGIBLE', 'target row not found');
  const values = sh.getRange(row, 1, 1, map._width).getValues()[0];
  const get = h => map[h] === undefined ? '' : values[map[h]];
  return {sh: sh, map: map, row: row, kind: kind, id: id, get: get,
    reply: String(get('reply') || '').trim(), status: String(get('reply_status') || '').trim(),
    authorId: idText_(get('author_id')), url: String(get('url') || ''), isReply: get('is_reply') === true || get('is_reply') === 'TRUE'};
}

function setStatus_(t, status, note) {
  const cell = t.sh.getRange(t.row, t.map.reply_status + 1);
  cell.setValue(status);
  if (note) cell.setNote(`${status}: ${note} @ ${nowIso_()}`);
  t.status = status;
}

function binding_(t, accountId) {
  return {account_id: accountId, kind: t.kind, id: t.id, target_author_id: t.authorId,
    url_hash: sha256Hex_(t.url).slice(0, 16), reply_sha256: sha256Hex_(t.reply)};
}

function eligibility_(ctx, t, accountId) {
  if (!t.reply) return 'no-reply';
  if (isHeld_(ctx, t.kind, t.id) || t.status === 'held') return 'held';
  if (isSent_(ctx, t.kind, t.id) || t.status === 'sent' || t.status === 'replied') return 'already-sent';
  if (t.status === 'declined') return 'declined';
  if (isUncertain_(ctx, t.kind, t.id) && recentUncertain_(ctx, t)) return 'uncertain-history';
  if (t.status === 'uncertain') return 'uncertain';
  if (t.status === 'sending') return 'in-flight';
  if (t.status && ['pending', 'failed'].indexOf(t.status) === -1) return 'status-' + t.status;
  if (t.status === 'failed') return 'failed';
  if (!t.authorId) return 'no-author-id';
  if (dncActive_(ctx, t.authorId)) return 'dnc';
  if (accountId && t.authorId === accountId) return 'own-comment';
  if (t.kind === 'comment' && t.isReply) return 'nested-target-disabled';
  if (t.kind === 'post' && !CF_CONFIG.POST_REPLIES) return 'post-replies-disabled';
  return '';
}

// محاولة ماعرفناش نتيجتها في آخر 24 ساعة: مفيش إعادة لحد ما تعدّي (فيسبوك ساعات بيأخر أو يخبّي التعليق)
function recentUncertain_(ctx, t) {
  const last = ledgerRows_(ctx).filter(r => r.kind === t.kind && r.target_id === t.id && r.status_to === 'uncertain').pop();
  return !!last && (Date.now() - new Date(last.ts).getTime()) < 24 * 3600 * 1000;
}

function leaseKey_(t) { return `LEASE.${t.kind}.${t.id}`; }
function readLease_(ctx, t) { try { return JSON.parse(nsGet_(ctx.ns, leaseKey_(t)) || 'null'); } catch (e) { return null; } }

function submitAuthorized_(ctx, claimId) {
  return ledgerRows_(ctx).some(r => r.claim_id === claimId && r.event === 'submit_authorized');
}

// عقد منتهي: لو اتصرّح بالإرسال = uncertain (ممكن يكون نزل)، لو لأ = pending (ماحدش ضغط Enter)
function expireLease_(ctx, t) {
  const lease = readLease_(ctx, t);
  if (!lease || lease.until > Date.now()) return false;
  const to = submitAuthorized_(ctx, lease.claim_id) ? 'uncertain' : 'pending';
  appendEvent_(ctx, {event: 'lease-expired', kind: t.kind, target_id: t.id, claim_id: lease.claim_id, fence: lease.fence,
    status_from: t.status, status_to: to, event_id: `expire:${lease.claim_id}`});
  if (to === 'uncertain') mirrorAdd_(ctx, 'UNCERT', setKey_(t.kind, t.id));
  setStatus_(t, to, 'lease expired');
  nsDel_(ctx.ns, leaseKey_(t));
  return true;
}

// ------------------------------------------------------------------ أوامر الردود

function rescanDeclined_(ctx, t) {
  if (t.status === 'declined' && t.authorId && !dncActive_(ctx, t.authorId)) {
    addDnc_(ctx, t.authorId, {kind: t.kind, id: t.id}, 'owner-declined (rescan)');
  }
}

function actionPendingReplies_(ctx) {
  const accountId = idText_(ctx.payload.account_id || '');
  checkLedgerHead_(ctx);
  const eligible = [], blocked = [];
  [['post', POSTS, POST_HEAD, 'post_id'], ['comment', COMMENTS, COMMENT_HEAD, 'comment_id']].forEach(([kind, name, head, idCol]) => {
    const sh = ctx.ss.getSheetByName(name);
    const map = requireV2Headers_(sh, head);
    const last = sh.getLastRow();
    if (last < 2) return;
    const values = sh.getRange(2, 1, last - 1, map._width).getValues();
    values.forEach((v, i) => {
      const id = idText_(v[map[idCol]]);
      const reply = String(v[map.reply] || '').trim();
      const status = String(v[map.reply_status] || '').trim();
      if (!id || (!reply && status !== 'declined')) return;
      const t = {sh: sh, map: map, row: i + 2, kind: kind, id: id, reply: reply, status: status,
        authorId: idText_(v[map.author_id]), url: String(v[map.url] || ''),
        isReply: v[map.is_reply] === true || v[map.is_reply] === 'TRUE'};
      rescanDeclined_(ctx, t);
      if (!reply) return;
      if (status === 'sending') expireLease_(ctx, t);
      // مسودة جديدة بعد الترحيل والحالة فاضية (onEdit مااشتغلش): pending
      if (!t.status && !isHeld_(ctx, kind, id) && !isSent_(ctx, kind, id)) setStatus_(t, 'pending');
      const why = eligibility_(ctx, t, accountId);
      if (why) { blocked.push({kind: kind, id: id, reason: why}); return; }
      eligible.push({kind: kind, id: id, post_id: idText_(v[map.post_id]), group_id: idText_(v[map.group_id]),
        parent_id: kind === 'comment' ? idText_(v[map.parent_id]) : '', url: t.url, author: String(v[map.author] || ''),
        author_id: t.authorId, reply: reply});
    });
  });
  return {eligible: eligible, blocked: blocked, count: eligible.length};
}

function actionClaim_(ctx) {
  const p = ctx.payload;
  const accountId = checkId_(p.account_id, 'account_id');
  if (capacityBlocksClaims_(ctx)) throw new ApiErr('E_CAPACITY', 'restriction mirrors over 90% of budget');
  checkLedgerHead_(ctx);
  const t = findTarget_(ctx, p.kind, idText_(p.id));
  rescanDeclined_(ctx, t);
  if (t.status === 'sending') expireLease_(ctx, t);
  if (!t.status && t.reply && !isHeld_(ctx, t.kind, t.id) && !isSent_(ctx, t.kind, t.id)) setStatus_(t, 'pending');
  const lease = readLease_(ctx, t);
  if (lease && lease.until > Date.now()) throw new ApiErr('E_CONFLICT', 'target already claimed');
  const why = eligibility_(ctx, t, accountId);
  if (why === 'dnc') throw new ApiErr('E_DNC', why);
  if (why === 'held') throw new ApiErr('E_HELD', why);
  if (why) throw new ApiErr('E_NOT_ELIGIBLE', why);
  const fence = ledgerRows_(ctx).filter(r => r.kind === t.kind && r.target_id === t.id && r.event === 'claim').length + 1;
  const claimId = `${ctx.req.request_id}`;
  const bind = binding_(t, accountId);
  const until = Date.now() + LEASE_MS;
  nsSet_(ctx.ns, leaseKey_(t), JSON.stringify({claim_id: claimId, fence: fence, until: until, binding: bind}));
  appendEvent_(ctx, {event: 'claim', kind: t.kind, target_id: t.id, post_id: idText_(t.get('post_id')),
    group_id: idText_(t.get('group_id')), author_id: t.authorId, claim_id: claimId, fence: fence, account_id: accountId,
    reply_sha256: bind.reply_sha256, status_from: t.status, status_to: 'sending'});
  setStatus_(t, 'sending');
  return {claim_id: claimId, fence: fence, lease_until: until, binding: bind,
    target: {kind: t.kind, id: t.id, url: t.url, author_id: t.authorId, reply: t.reply,
      parent_id: idText_(t.get('parent_id')), post_id: idText_(t.get('post_id')), group_id: idText_(t.get('group_id'))}};
}

function leaseFor_(ctx, t, p) {
  const lease = readLease_(ctx, t);
  if (!lease || lease.claim_id !== p.claim_id || Number(lease.fence) !== Number(p.fence)) throw new ApiErr('E_CONFLICT', 'not the current claim');
  if (lease.until <= Date.now()) throw new ApiErr('E_CONFLICT', 'lease expired');
  return lease;
}

function actionRenew_(ctx) {
  const p = ctx.payload;
  const t = findTarget_(ctx, p.kind, idText_(p.id));
  const lease = leaseFor_(ctx, t, p);
  lease.until = Date.now() + LEASE_MS;
  nsSet_(ctx.ns, leaseKey_(t), JSON.stringify(lease));
  return {lease_until: lease.until};
}

function actionRelease_(ctx) {
  const p = ctx.payload;
  const t = findTarget_(ctx, p.kind, idText_(p.id));
  const lease = readLease_(ctx, t);
  if (!lease || lease.claim_id !== p.claim_id) return {released: false, reason: 'not current claim'};
  const to = submitAuthorized_(ctx, lease.claim_id) ? 'uncertain' : 'pending';
  appendEvent_(ctx, {event: 'release', kind: t.kind, target_id: t.id, claim_id: lease.claim_id, fence: lease.fence,
    status_from: t.status, status_to: to});
  if (to === 'uncertain') mirrorAdd_(ctx, 'UNCERT', setKey_(t.kind, t.id));
  setStatus_(t, to, p.note || 'released');
  nsDel_(ctx.ns, leaseKey_(t));
  return {released: true, status: to};
}

// آخر فحص قبل Enter: العقد سليم، والسور (fence) هو الحالي، والمسودة والشخص والرابط زي ما اتحجزوا، ومفيش منع
function actionPrepareSubmit_(ctx) {
  const p = ctx.payload;
  const t = findTarget_(ctx, p.kind, idText_(p.id));
  const lease = leaseFor_(ctx, t, p);
  const now = binding_(t, lease.binding.account_id);
  const want = p.binding || {};
  ['account_id', 'kind', 'id', 'target_author_id', 'url_hash', 'reply_sha256'].forEach(k => {
    if (String(now[k]) !== String(lease.binding[k]) || String(want[k]) !== String(lease.binding[k])) {
      throw new ApiErr('E_CONFLICT', `binding changed: ${k}`);
    }
  });
  if (t.status !== 'sending') throw new ApiErr('E_CONFLICT', `status is ${t.status}`);
  rescanDeclined_(ctx, t);
  if (dncActive_(ctx, t.authorId)) throw new ApiErr('E_DNC', 'author on do-not-contact');
  if (isHeld_(ctx, t.kind, t.id)) throw new ApiErr('E_HELD', 'held');
  if (isSent_(ctx, t.kind, t.id)) throw new ApiErr('E_NOT_ELIGIBLE', 'already-sent');
  const deadline = Date.now() + SUBMIT_WINDOW_MS;
  appendEvent_(ctx, {event: 'submit_authorized', kind: t.kind, target_id: t.id, claim_id: lease.claim_id,
    fence: lease.fence, account_id: lease.binding.account_id, reply_sha256: now.reply_sha256, note: `deadline ${deadline}`});
  return {authorized: true, deadline: deadline};
}

const MARK_FROM = {
  sent: ['sending', 'uncertain'], uncertain: ['sending'], failed: ['sending', 'uncertain'],
  declined: ['sending', 'uncertain', 'sent', 'replied', 'pending', 'failed']
};

function actionMarkStatus_(ctx) {
  const p = ctx.payload;
  const to = String(p.to || '');
  if (!MARK_FROM[to]) throw new ApiErr('E_VALIDATION', 'to must be sent|uncertain|failed|declined');
  const t = findTarget_(ctx, p.kind, idText_(p.id));
  if (String(p.expected_status || '') !== t.status) throw new ApiErr('E_CONFLICT', `status is ${t.status}, expected ${p.expected_status}`);
  if (MARK_FROM[to].indexOf(t.status) === -1) throw new ApiErr('E_CONFLICT', `cannot go ${t.status} -> ${to}`);
  const lease = readLease_(ctx, t);
  const claimId = String(p.claim_id || '');
  if (t.status === 'sending' && (!lease || lease.claim_id !== claimId || Number(lease.fence) !== Number(p.fence))) {
    throw new ApiErr('E_CONFLICT', 'not the current claim');
  }
  // بعد ما اتصرّح بالإرسال مفيش «failed» من الماكينة: ممكن يكون نزل، فبيبقى uncertain لحد ما نتأكد
  if (to === 'failed' && t.status === 'sending' && submitAuthorized_(ctx, claimId)) {
    throw new ApiErr('E_CONFLICT', 'submit was authorized: use uncertain');
  }
  if (to === 'sent' && !ID_RE.test(idText_(p.sent_reply_id || ''))) throw new ApiErr('E_VALIDATION', 'sent needs sent_reply_id');
  appendEvent_(ctx, {event: 'status', kind: t.kind, target_id: t.id, author_id: t.authorId, claim_id: claimId,
    fence: p.fence || '', account_id: p.account_id || '', sent_reply_id: idText_(p.sent_reply_id || ''),
    sent_at: p.sent_at || '', status_from: t.status, status_to: to, note: p.note || ''});
  if (to === 'sent') mirrorAdd_(ctx, 'SENT', setKey_(t.kind, t.id));
  if (to === 'uncertain') mirrorAdd_(ctx, 'UNCERT', setKey_(t.kind, t.id));
  if (to === 'declined') addDnc_(ctx, t.authorId, {kind: t.kind, id: t.id}, p.note || 'declined');
  setStatus_(t, to, p.note || '');
  if (lease) nsDel_(ctx.ns, leaseKey_(t));
  return {status: to};
}

function actionSweep_(ctx) {
  const intact = verifyChain_(ctx);
  if (!intact) throw integrityFail_(ctx, 'ledger chain broken');
  const out = actionPendingReplies_(ctx);
  // المرايات من الدفتر: أي حدث ماوصلش لمرايته بيتكمّل (اتحاد، مفيش حذف)
  let repaired = 0;
  ledgerRows_(ctx).forEach(r => {
    const before = repaired;
    if (r.event === 'legacy-sent' || r.status_to === 'sent') { if (!mirrorLoad_(ctx, 'SENT').has(setKey_(r.kind, r.target_id))) { mirrorAdd_(ctx, 'SENT', setKey_(r.kind, r.target_id)); repaired++; } }
    if (r.event === 'held-legacy') { if (!mirrorLoad_(ctx, 'HELD').has(setKey_(r.kind, r.target_id))) { mirrorAdd_(ctx, 'HELD', setKey_(r.kind, r.target_id)); repaired++; } }
    if (r.status_to === 'uncertain') { if (!mirrorLoad_(ctx, 'UNCERT').has(setKey_(r.kind, r.target_id))) { mirrorAdd_(ctx, 'UNCERT', setKey_(r.kind, r.target_id)); repaired++; } }
    if (r.event === 'dnc-add' && r.author_id) { if (!mirrorLoad_(ctx, 'DNC').has(setKey_('author', r.author_id))) { mirrorAdd_(ctx, 'DNC', setKey_('author', r.author_id)); repaired++; } }
    return before;
  });
  return {intact: intact, repaired: repaired, eligible: out.count, blocked: out.blocked.length,
    mirror_bytes: mirrorBytes_(ctx.ns)};
}

function actionDncList_(ctx) {
  return {dnc: Array.from(mirrorLoad_(ctx, 'DNC')).length};
}

// ------------------------------------------------------------------ النتايج من السحب: replied و declined

function detectOutcomes_(ctx, comments, data) {
  const rows = ctx.ss.getSheetByName(LEDGER) ? ledgerRows_(ctx) : [];
  if (!rows.length) return {replied: 0, declined: 0};
  const sentByReply = {};
  rows.forEach(r => {
    if (r.status_to === 'sent' && r.sent_reply_id) sentByReply[r.sent_reply_id] = r;
  });
  let replied = 0, declined = 0;
  comments.forEach(c => {
    // رفض على ردنا نفسه بس (المقالة اللي الـ id بتاعها = sent_reply_id)، مش أي نص في الصفحة
    const own = sentByReply[c.comment_id];
    if (own && /declined|rejected/i.test(c.moderation || '')) {
      const t = findTarget_(ctx, own.kind, own.target_id);
      if (['sent', 'replied'].indexOf(t.status) !== -1) {
        appendEvent_(ctx, {event: 'status', kind: t.kind, target_id: t.id, author_id: t.authorId, sent_reply_id: c.comment_id,
          status_from: t.status, status_to: 'declined', note: 'facebook declined our reply',
          event_id: `declined:${c.comment_id}`});
        addDnc_(ctx, t.authorId, {kind: t.kind, id: t.id}, 'facebook declined our reply');
        setStatus_(t, 'declined', 'facebook declined our reply');
        declined++;
      }
      return;
    }
    // رد على ردنا بالظبط: متداخل تحت ردنا (nest_parent_id)، أو لو الهدف منشور: الـ thread أبوه تعليقنا
    const parentSent = sentByReply[c.nest_parent_id] || (sentByReply[c.parent_id] && sentByReply[c.parent_id].kind === 'post' ? sentByReply[c.parent_id] : null);
    if (!parentSent || !c.author_id) return;
    if (parentSent.kind === 'comment' && sentByReply[c.parent_id] && !c.nest_parent_id) return;
    const t = findTarget_(ctx, parentSent.kind, parentSent.target_id);
    if (!t.authorId || c.author_id !== t.authorId || t.status !== 'sent') return;
    const sentAt = parentSent.sent_at ? new Date(parentSent.sent_at).getTime() : NaN;
    if (!(c.created_time instanceof Date) || isNaN(sentAt) || c.created_time.getTime() <= sentAt) return;
    appendEvent_(ctx, {event: 'status', kind: t.kind, target_id: t.id, author_id: t.authorId, sent_reply_id: parentSent.sent_reply_id,
      status_from: 'sent', status_to: 'replied', note: `answered by ${c.comment_id}`, event_id: `replied:${t.kind}:${t.id}`});
    setStatus_(t, 'replied', `answered by ${c.comment_id}`);
    replied++;
  });
  return {replied: replied, declined: declined};
}

// ------------------------------------------------------------------ onEdit (راحة بس؛ السيرفر بيعيد الفحص)

function onEdit(e) {
  try {
    if (!e || !e.range) return;
    const ss = e.source || SpreadsheetApp.getActiveSpreadsheet();
    const ns = nsOf_(ss.getId());
    if (schemaOf_(ns) !== SCHEMA_V2) return;
    const sh = e.range.getSheet();
    const name = sh.getName();
    if (name !== POSTS && name !== COMMENTS) return;
    const kind = name === POSTS ? 'post' : 'comment';
    const map = headerMap_(sh);
    if (map.reply === undefined || map.reply_status === undefined) return;
    const c1 = e.range.getColumn() - 1, c2 = c1 + e.range.getNumColumns() - 1;
    const touchesReply = map.reply >= c1 && map.reply <= c2;
    const touchesStatus = map.reply_status >= c1 && map.reply_status <= c2;
    if (!touchesReply && !touchesStatus) return;
    const lock = LockService.getDocumentLock();
    if (!lock.tryLock(5000)) return;
    try {
      const ctx = {ss: ss, ns: ns, req: {request_id: 'onedit-' + Date.now()}, warnings: []};
      const r1 = Math.max(e.range.getRow(), 2), r2 = e.range.getRow() + e.range.getNumRows() - 1;
      const idCol = kind === 'post' ? 'post_id' : 'comment_id';
      for (let row = r1; row <= r2; row++) {
        const v = sh.getRange(row, 1, 1, map._width).getValues()[0];
        const id = idText_(v[map[idCol]]);
        if (!id) continue;
        const t = {sh: sh, map: map, row: row, kind: kind, id: id, reply: String(v[map.reply] || '').trim(),
          status: String(v[map.reply_status] || '').trim(), authorId: idText_(v[map.author_id])};
        const locked = isHeld_(ctx, kind, id) || isSent_(ctx, kind, id) || dncDeclinedRow_(ctx, t);
        if (touchesStatus && e.range.getNumRows() * e.range.getNumColumns() === 1 && e.oldValue !== undefined) {
          const was = String(e.oldValue || '');
          if (was === 'declined' && t.status !== 'declined') { setStatus_(t, 'declined', 'declined is permanent'); continue; }
          if (t.status === 'pending' && (locked || ['held', 'sent', 'replied', 'uncertain'].indexOf(was) !== -1)) {
            setStatus_(t, was || 'held', 'cannot set pending here'); continue;
          }
        }
        if (t.status === 'declined' && t.authorId) { addDnc_(ctx, t.authorId, {kind: kind, id: id}, 'owner-declined'); continue; }
        if (!touchesReply || locked) continue;
        if (t.reply && !t.status) setStatus_(t, 'pending');
        else if (!t.reply && t.status === 'pending') t.sh.getRange(row, map.reply_status + 1).setValue('');
      }
    } finally { lock.releaseLock(); }
  } catch (err) {
    // onEdit مابيرجعش أخطاء لحد؛ السيرفر بيصلّح في أول فحص
  }
}

function dncDeclinedRow_(ctx, t) { return t.status === 'declined'; }

// ------------------------------------------------------------------ جدول الأوامر

const ACTIONS = {
  ingest: {auth: 'ingest', target: 'none', lock: true, replay: true, schema: SCHEMA_V2, maintenance: true, run: actionIngest_},
  ingest_as: {auth: 'admin', target: 'write', lock: true, replay: true, schema: SCHEMA_V2, maintenance: true, run: ctx => {
    if (ctx.production) throw new ApiErr('E_TARGET_NOT_ALLOWED', 'use ingest for production');
    return actionIngest_(ctx);
  }},
  pending_replies: {auth: 'admin', target: 'write', lock: true, replay: false, schema: SCHEMA_V2, maintenance: true, replies: true, run: actionPendingReplies_},
  claim: {auth: 'admin', target: 'write', lock: true, replay: true, schema: SCHEMA_V2, maintenance: true, replies: true, run: actionClaim_},
  renew: {auth: 'admin', target: 'write', lock: true, replay: false, schema: SCHEMA_V2, replies: true, run: actionRenew_},
  release_claim: {auth: 'admin', target: 'write', lock: true, replay: true, schema: SCHEMA_V2, run: actionRelease_},
  prepare_submit: {auth: 'admin', target: 'write', lock: true, replay: true, schema: SCHEMA_V2, maintenance: true, replies: true, run: actionPrepareSubmit_},
  mark_status: {auth: 'admin', target: 'write', lock: true, replay: true, schema: SCHEMA_V2, run: actionMarkStatus_},
  sweep_restrictions: {auth: 'admin', target: 'write', lock: true, replay: false, schema: SCHEMA_V2, run: actionSweep_},
  dnc_list: {auth: 'admin', target: 'read', lock: false, schema: SCHEMA_V2, run: actionDncList_}
};
