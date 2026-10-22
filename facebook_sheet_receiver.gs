/**
 * مستقبل بيانات جروب فيسبوك -> Google Sheet
 * انشره كـ Web App (Execute as: Me, Who has access: Anyone).
 * بعد أي تعديل: Deploy > Manage deployments > Edit > New version > Deploy.
 *
 * ممنوع تكرار البوست أو التعليق:
 *   Posts    يتفرد بـ post_id
 *   Comments يتفرد بـ comment_id
 * لو نفس المعرف وصل تاني، الصف يتحدث ومش يتضاف.
 * عمود reply الفاضي مايمسحش رد مكتوب قبل كده.
 */
const TOKEN = 'CommentSecrets';
const POSTS = 'Posts';
const COMMENTS = 'Comments';
const POST_HEAD = [
  'post_id', 'group_id', 'author', 'author_id', 'text', 'created_time',
  'url', 'permalink', 'post content', 'comment_sort', 'received_at'
];
const COMMENT_HEAD = [
  'comment_id', 'post_id', 'group_id', 'parent_id', 'reply_comment_id',
  'is_reply', 'reply_target_id', 'author', 'author_id', 'author_url',
  'text', 'created_time', 'permalink', 'post content', 'reply', 'reply_status', 'received_at'
];
const KEEP_IF_BLANK = ['reply', 'reply_status'];

function setup() {
  const ps = sheet_(POSTS, POST_HEAD);
  const cs = sheet_(COMMENTS, COMMENT_HEAD);
  ensureHeaders_(ps, POST_HEAD);
  ensureHeaders_(cs, COMMENT_HEAD);
  dedupeSheet_(ps);
  dedupeSheet_(cs);
}

function doGet(e) {
  const params = (e && e.parameter) || {};
  if (params.action === 'pending_replies') {
    if (params.token !== TOKEN) return json_({ok: false, error: 'bad token'});
    const cs = sheet_(COMMENTS, COMMENT_HEAD);
    ensureHeaders_(cs, COMMENT_HEAD);
    const cols = headerMap_(cs);
    const last = cs.getLastRow();
    const rows = [];
    if (last > 1) {
      const values = cs.getRange(2, 1, last - 1, cols._width).getValues();
      values.forEach(row => {
        const reply = String(row[cols.reply] || '').trim();
        const status = String(row[cols.reply_status] || '').trim();
        if (!reply || status === 'posted') return;
        rows.push({
          comment_id: idText_(row[cols.comment_id]),
          post_id: idText_(row[cols.post_id]),
          group_id: idText_(row[cols.group_id]),
          parent_id: idText_(row[cols.parent_id]),
          permalink: row[cols.permalink] || '',
          author: row[cols.author] || '',
          reply: reply
        });
      });
    }
    return json_({ok: true, count: rows.length, replies: rows});
  }
  return json_({ok: true, msg: 'receiver is running', dedupe: 'post_id/comment_id'});
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    // جوه try: لو القفل مشغول، الرد يبقى JSON {ok:false} مش صفحة خطأ HTML
    lock.waitLock(30000);
    const body = JSON.parse(e.postData.contents || '{}');
    if (body.token !== TOKEN) return json_({ok: false, error: 'bad token'});
    if (body.action === 'mark_replied') {
      const cs = sheet_(COMMENTS, COMMENT_HEAD);
      const cols = headerMap_(cs);
      let marked = 0;
      (body.comment_ids || []).forEach(id => {
        const hit = findIdRow_(cs, idText_(id));
        if (!hit || cols.reply_status === undefined) return;
        cs.getRange(hit, cols.reply_status + 1).setValue('posted');
        marked++;
      });
      return json_({ok: true, marked: marked});
    }

    const ps = sheet_(POSTS, POST_HEAD);
    const cs = sheet_(COMMENTS, COMMENT_HEAD);
    ensureHeaders_(ps, POST_HEAD);
    ensureHeaders_(cs, COMMENT_HEAD);
    forceIdText_(ps);
    forceIdText_(cs);
    const removedPosts = dedupeSheet_(ps);
    const removedComments = dedupeSheet_(cs);
    const postCols = headerMap_(ps);
    const commentCols = headerMap_(cs);
    const now = new Date();
    const seenPosts = {};
    const seenComments = {};
    const postItems = [];
    const commentItems = [];

    (body.posts || []).forEach(p => {
      const postId = idText_(p.post_id);
      if (!postId || seenPosts[postId]) return;
      seenPosts[postId] = true;
      const postValues = {
        post_id: postId,
        group_id: idText_(body.group_id || p.group_id || ''),
        author: p.author || '',
        author_id: idText_(p.author_id || ''),
        text: p.text || '',
        created_time: p.created_time || '',
        url: p.url || p.permalink || '',
        permalink: p.permalink || p.url || '',
        'post content': p['post content'] || p.post_content || p.text || '',
        comment_sort: p.comment_sort || '',
        received_at: now
      };
      postItems.push({id: postId, values: postValues});

      (p.comments || []).forEach(c => {
        const commentId = idText_(c.comment_id);
        if (!commentId || seenComments[commentId]) return;
        seenComments[commentId] = true;
        const commentValues = {
          comment_id: commentId,
          post_id: postId,
          group_id: idText_(body.group_id || p.group_id || ''),
          parent_id: idText_(c.parent_id || c.parent_comment_id || ''),
          reply_comment_id: idText_(c.reply_comment_id || ''),
          is_reply: c.is_reply ? 'yes' : '',
          reply_target_id: idText_(c.reply_target_id || commentId),
          author: c.author || '',
          author_id: idText_(c.author_id || ''),
          author_url: c.author_url || '',
          text: c.text || '',
          created_time: c.created_time || '',
          permalink: c.permalink || '',
          'post content': c['post content'] || p['post content'] || p.post_content || p.text || '',
          reply: c.reply || '',
          reply_status: c.reply_status || '',
          received_at: now
        };
        commentItems.push({id: commentId, values: commentValues});
      });
    });
    const [np, up] = upsertMany_(ps, postCols, postItems);
    const [nc, uc] = upsertMany_(cs, commentCols, commentItems);
    return json_({
      ok: true,
      posts_new: np,
      posts_updated: up,
      comments_new: nc,
      comments_updated: uc,
      duplicates_removed: removedPosts + removedComments
    });
  } catch (err) {
    return json_({ok: false, error: String(err)});
  } finally {
    lock.releaseLock();
  }
}

function idText_(value) {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'number') {
    return value.toLocaleString('en-US', {useGrouping: false, maximumFractionDigits: 0});
  }
  return String(value).replace(/^'/, '').trim();
}

// keys: أسماء الأعمدة اللي الصف ده بيكتبها. أي عمود تاني (عمود ضافه صاحب الشيت) بيفضل زي ما هو.
function upsert_(sh, id, row, cols, keys) {
  const key = idText_(id);
  if (!key) throw new Error('missing id');
  const hit = findIdRow_(sh, key);
  if (hit) {
    updateRow_(sh, hit, row, cols, keys);
    return false;
  }
  writeRows_(sh, sh.getLastRow() + 1, [row]);
  return true;
}

// كل صفوف الطلب مرة واحدة: عمود الـ id بيتقري مرة، والصفوف الجديدة بتتكتب في نداء واحد.
// قبل كده كل صف كان بيقرا عمود الـ id كله ويكتب لوحده (2–3 نداءات) تحت القفل، فالوقت بيكبر
// مع كل تعليق في الشيت — آلاف التعليقات كانت هتاخد ساعات. بيرجّع [جديد، اتحدّث].
function upsertMany_(sh, cols, items) {
  const index = {};
  const last = sh.getLastRow();
  if (last >= 2) {
    sh.getRange(2, 1, last - 1, 1).getValues().forEach((r, i) => {
      const key = idText_(r[0]);
      if (key && !index[key]) index[key] = i + 2;
    });
  }
  const fresh = [];
  let updated = 0;
  items.forEach(item => {
    const key = idText_(item.id);
    if (!key) throw new Error('missing id');
    const row = rowFrom_(cols, item.values);
    if (index[key]) {
      updateRow_(sh, index[key], row, cols, Object.keys(item.values));
      updated++;
    } else {
      fresh.push(row);
    }
  });
  if (fresh.length) writeRows_(sh, last + 1, fresh);
  return [fresh.length, updated];
}

// الأعمدة اللي الصف ده مش بيكتبها (عمود ضافه صاحب الشيت) بتفضل زي ما هي، والرد المكتوب مابيتمسحش
function updateRow_(sh, hit, row, cols, keys) {
  const current = sh.getRange(hit, 1, 1, row.length).getValues()[0];
  const owned = {};
  (keys || Object.keys(cols)).forEach(name => { if (cols[name] !== undefined) owned[cols[name]] = true; });
  for (let i = 0; i < row.length; i++) {
    if (!owned[i]) row[i] = current[i];
  }
  KEEP_IF_BLANK.forEach(name => {
    const index = cols[name];
    if (index === undefined) return;
    if (!row[index] && current[index]) row[index] = current[index];
  });
  writeRows_(sh, hit, [row]);
}

// الـ ids نص ('@') قبل الكتابة، وإلا الشيت بيحوّل الرقم الطويل لـ Number ويضيّع آخره
function writeRows_(sh, first, rows) {
  const range = sh.getRange(first, 1, rows.length, rows[0].length);
  range.setNumberFormat('@');
  range.setValues(rows);
}

function findIdRow_(sh, id) {
  const last = sh.getLastRow();
  if (last < 2) return 0;
  const values = sh.getRange(2, 1, last - 1, 1).getValues();
  for (let i = 0; i < values.length; i++) {
    if (idText_(values[i][0]) === id) return i + 2;
  }
  return 0;
}

function dedupeSheet_(sh) {
  const last = sh.getLastRow();
  if (last < 3) return 0;
  const values = sh.getRange(2, 1, last - 1, 1).getValues();
  const seen = {};
  const drop = [];
  for (let i = 0; i < values.length; i++) {
    const key = idText_(values[i][0]);
    if (!key) continue;
    if (seen[key]) drop.push(i + 2);
    else seen[key] = i + 2;
  }
  drop.sort((a, b) => b - a).forEach(row => sh.deleteRow(row));
  return drop.length;
}

function forceIdText_(sh) {
  const last = Math.max(sh.getLastRow(), 2);
  sh.getRange(2, 1, last - 1, 1).setNumberFormat('@');
}

function sheet_(name, head) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  if (sh.getLastRow() === 0) {
    sh.appendRow(head);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, head.length).setFontWeight('bold');
    sh.getRange('A:A').setNumberFormat('@');
  }
  return sh;
}

function ensureHeaders_(sh, head) {
  const width = Math.max(sh.getLastColumn(), head.length, 1);
  const current = sh.getRange(1, 1, 1, width).getValues()[0].map(v => String(v || ''));
  const missing = head.filter(name => current.indexOf(name) === -1);
  if (!missing.length && current[0]) return;
  // الفراغات اللي في النص بتفضل مكانها: لو اتشالت، العناوين اللي بعدها بتزحف عن بياناتها
  let lastUsed = current.length - 1;
  while (lastUsed >= 0 && !current[lastUsed]) lastUsed--;
  const next = current[0] ? current.slice(0, lastUsed + 1).concat(missing) : head;
  sh.getRange(1, 1, 1, next.length).setValues([next]);
  sh.getRange(1, 1, 1, next.length).setFontWeight('bold');
  sh.setFrozenRows(1);
  sh.getRange('A:A').setNumberFormat('@');
}

function headerMap_(sh) {
  const width = Math.max(sh.getLastColumn(), 1);
  const head = sh.getRange(1, 1, 1, width).getValues()[0];
  const map = {};
  head.forEach((name, i) => { if (name) map[String(name)] = i; });
  map._width = head.length;
  return map;
}

function rowFrom_(map, values) {
  const row = new Array(map._width).fill('');
  Object.keys(values).forEach(key => {
    if (map[key] !== undefined) row[map[key]] = values[key];
  });
  return row;
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
