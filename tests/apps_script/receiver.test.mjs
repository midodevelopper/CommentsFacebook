// Executable tests for facebook_sheet_receiver.gs (the real file, run in a vm with fake services).
// Run alone:  node --test tests/apps_script/
// Each test name must be unique and single-quoted: tests/test_apps_script.py lists them by regex.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadReceiver } from './gas_fake.mjs';

const TOKEN = 'CommentSecrets';
const G = '4678007325765117';

function body(posts, extra = {}) { return { token: TOKEN, group_id: G, posts, ...extra }; }
function comment(id, extra = {}) { return { comment_id: id, text: 't' + id, author: 'a', ...extra }; }
function post(id, comments = [], extra = {}) { return { post_id: id, text: 'p' + id, comments, ...extra }; }

// ------------------------------------------------------------ contracts

test('contract: token constant', () => {
  assert.equal(loadReceiver().consts.TOKEN, TOKEN);
});

test('contract: Posts headers exact order', () => {
  assert.deepEqual([...loadReceiver().consts.POST_HEAD], ['post_id', 'group_id', 'author', 'author_id', 'text',
    'created_time', 'url', 'permalink', 'post content', 'comment_sort', 'received_at']);
});

test('contract: Comments headers exact order', () => {
  assert.deepEqual([...loadReceiver().consts.COMMENT_HEAD], ['comment_id', 'post_id', 'group_id', 'parent_id',
    'reply_comment_id', 'is_reply', 'reply_target_id', 'author', 'author_id', 'author_url', 'text', 'created_time',
    'permalink', 'post content', 'reply', 'reply_status', 'received_at']);
});

test('contract: reply and reply_status are keep-if-blank', () => {
  assert.deepEqual([...loadReceiver().consts.KEEP_IF_BLANK], ['reply', 'reply_status']);
});

test('setup creates both tabs with headers, frozen bold row and text id column', () => {
  const r = loadReceiver();
  r.call('setup');
  const posts = r.ss.getSheetByName('Posts'), comments = r.ss.getSheetByName('Comments');
  assert.deepEqual(posts.table()[0], [...r.consts.POST_HEAD]);
  assert.deepEqual(comments.table()[0], [...r.consts.COMMENT_HEAD]);
  assert.equal(posts.frozenRows, 1);
  assert.equal(comments.columnFormats[1], '@');
});

// ------------------------------------------------------------ doGet

test('doGet without action reports running', () => {
  assert.deepEqual(loadReceiver().get({}), { ok: true, msg: 'receiver is running', dedupe: 'post_id/comment_id' });
});

test('doGet with no event object at all', () => {
  const r = loadReceiver();
  assert.equal(JSON.parse(r.context.doGet(undefined).content).ok, true);
});

test('doGet returns JSON mime type', () => {
  const r = loadReceiver();
  assert.equal(r.context.doGet({ parameter: {} }).mime, 'application/json');
});

test('pending_replies rejects bad token', () => {
  assert.deepEqual(loadReceiver().get({ action: 'pending_replies', token: 'nope' }), { ok: false, error: 'bad token' });
});

test('pending_replies on empty sheet creates Comments tab and returns none', () => {
  const r = loadReceiver();
  assert.deepEqual(r.get({ action: 'pending_replies', token: TOKEN }), { ok: true, count: 0, replies: [] });
  assert.ok(r.ss.getSheetByName('Comments'));
});

function seedForPending() {
  const r = loadReceiver();
  r.post(body([post('10', [
    comment('901', { reply: 'شكرا' }),
    comment('902', { reply: '   ' }),
    comment('903'),
    comment('904', { reply: 'done', reply_status: 'posted' }),
    comment('905', { reply: '  مرحبا  ', reply_status: 'failed' }),
    comment('906', { reply: 'r', parent_id: '901', reply_comment_id: '906', is_reply: true,
                     permalink: 'https://www.facebook.com/groups/G/posts/10/?comment_id=901&reply_comment_id=906' }),
  ])]));
  return r;
}

test('pending_replies selects rows with reply and status not posted', () => {
  const out = seedForPending().get({ action: 'pending_replies', token: TOKEN });
  assert.equal(out.ok, true);
  assert.deepEqual(out.replies.map(x => x.comment_id), ['901', '905', '906']);
  assert.equal(out.count, 3);
});

test('pending_replies trims reply text and returns the row contract', () => {
  const out = seedForPending().get({ action: 'pending_replies', token: TOKEN });
  const row = out.replies.find(x => x.comment_id === '905');
  assert.deepEqual(Object.keys(row), ['comment_id', 'post_id', 'group_id', 'parent_id', 'permalink', 'author', 'reply']);
  assert.equal(row.reply, 'مرحبا');
  assert.equal(row.post_id, '10');
  assert.equal(row.group_id, G);
  const reply = out.replies.find(x => x.comment_id === '906');
  assert.equal(reply.parent_id, '901');
  assert.match(reply.permalink, /reply_comment_id=906$/);
});

test('pending_replies status other than posted stays pending', () => {
  const out = seedForPending().get({ action: 'pending_replies', token: TOKEN });
  assert.ok(out.replies.some(x => x.comment_id === '905'));
});

test('pending_replies does not take the script lock', () => {
  const r = seedForPending();
  const before = r.lock.waits.length;
  r.get({ action: 'pending_replies', token: TOKEN });
  assert.equal(r.lock.waits.length, before);
});

// ------------------------------------------------------------ doPost: auth, parsing, locking

test('doPost rejects bad token and writes nothing', () => {
  const r = loadReceiver();
  assert.deepEqual(r.post({ token: 'x', posts: [post('1')] }), { ok: false, error: 'bad token' });
  assert.equal(r.ss.getSheetByName('Posts'), null);
});

test('doPost invalid JSON body returns ok false', () => {
  const out = loadReceiver().post('{not json');
  assert.equal(out.ok, false);
  assert.match(out.error, /SyntaxError/);
});

test('doPost without postData returns ok false', () => {
  const r = loadReceiver();
  const out = JSON.parse(r.context.doPost({}).content);
  assert.equal(out.ok, false);
  assert.match(out.error, /TypeError/);
});

test('doPost empty body string is treated as {} and rejected by token check', () => {
  assert.deepEqual(loadReceiver().post(''), { ok: false, error: 'bad token' });
});

test('doPost takes the lock for 30s and always releases it', () => {
  const r = loadReceiver();
  r.post({ token: 'x' });
  r.post('{bad');
  r.post(body([post('1')]));
  assert.deepEqual(r.lock.waits, [30000, 30000, 30000]);
  assert.equal(r.lock.released, 3);
});

test('doPost lock timeout throws instead of returning JSON', () => {
  // Characterization (defect D-06): waitLock is outside the try, so a busy lock is an uncaught
  // exception: Apps Script answers with an HTML error page, not {"ok": false}.
  const r = loadReceiver({ lockTimeout: true });
  assert.throws(() => r.context.doPost({ postData: { contents: JSON.stringify(body([])) } }), /Lock timeout/);
});

// ------------------------------------------------------------ doPost: upsert

test('upsert inserts new posts and comments with counts', () => {
  const r = loadReceiver();
  const out = r.post(body([post('10', [comment('901'), comment('902')]), post('11', [comment('903')])]));
  assert.deepEqual(out, { ok: true, posts_new: 2, posts_updated: 0, comments_new: 3, comments_updated: 0, duplicates_removed: 0 });
  assert.deepEqual(r.ss.getSheetByName('Posts').records().map(x => x.post_id), ['10', '11']);
  assert.deepEqual(r.ss.getSheetByName('Comments').records().map(x => x.comment_id), ['901', '902', '903']);
});

test('upsert resending the same ids updates rows instead of adding', () => {
  const r = loadReceiver();
  r.post(body([post('10', [comment('901')])]));
  const out = r.post(body([post('10', [comment('901', { text: 'edited' })])]));
  assert.deepEqual([out.posts_new, out.posts_updated, out.comments_new, out.comments_updated], [0, 1, 0, 1]);
  const rows = r.ss.getSheetByName('Comments').records();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].text, 'edited');
});

test('upsert ignores duplicate ids inside one request', () => {
  const r = loadReceiver();
  const out = r.post(body([post('10', [comment('901'), comment('901', { text: 'second' })]), post('10', [comment('902')])]));
  assert.deepEqual([out.posts_new, out.posts_updated, out.comments_new], [1, 0, 1]);
  assert.equal(r.ss.getSheetByName('Comments').records()[0].text, 't901');
  // the duplicate post's comments are skipped along with it
  assert.deepEqual(r.ss.getSheetByName('Comments').records().map(x => x.comment_id), ['901']);
});

test('upsert skips posts and comments without ids', () => {
  const r = loadReceiver();
  const out = r.post(body([post('', [comment('901')]), post('10', [comment(''), comment(null), comment('902')])]));
  assert.deepEqual([out.posts_new, out.comments_new], [1, 1]);
  assert.deepEqual(r.ss.getSheetByName('Comments').records().map(x => x.comment_id), ['902']);
});

test('upsert empty posts list is ok and creates tabs', () => {
  const r = loadReceiver();
  assert.deepEqual(r.post(body([])), { ok: true, posts_new: 0, posts_updated: 0, comments_new: 0, comments_updated: 0, duplicates_removed: 0 });
  assert.ok(r.ss.getSheetByName('Posts') && r.ss.getSheetByName('Comments'));
});

test('upsert missing posts key is ok', () => {
  assert.equal(loadReceiver().post({ token: TOKEN }).ok, true);
});

// ------------------------------------------------------------ doPost: field mapping

test('mapping post fields and fallbacks', () => {
  const r = loadReceiver();
  r.post(body([{ post_id: '10', author: 'A', author_id: '5', text: 'T', created_time: 'CT', permalink: 'PL', comment_sort: 'الأحدث' }]));
  const row = r.ss.getSheetByName('Posts').records()[0];
  assert.equal(row.group_id, G);
  assert.equal(row.url, 'PL');            // url falls back to permalink
  assert.equal(row.permalink, 'PL');
  assert.equal(row['post content'], 'T'); // post content falls back to text
  assert.equal(row.comment_sort, 'الأحدث');
  assert.equal(Object.prototype.toString.call(row.received_at), '[object Date]');   // vm-realm Date
});

test('mapping post content precedence', () => {
  const r = loadReceiver();
  r.post(body([{ post_id: '1', 'post content': 'A', post_content: 'B', text: 'C' },
               { post_id: '2', post_content: 'B', text: 'C' }]));
  assert.deepEqual(r.ss.getSheetByName('Posts').records().map(x => x['post content']), ['A', 'B']);
});

test('mapping body group_id wins over post group_id', () => {
  const r = loadReceiver();
  r.post({ token: TOKEN, group_id: '111', posts: [post('1', [], { group_id: '222' })] });
  r.post({ token: TOKEN, posts: [post('2', [], { group_id: '222' })] });
  assert.deepEqual(r.ss.getSheetByName('Posts').records().map(x => x.group_id), ['111', '222']);
});

test('mapping comment fields and fallbacks', () => {
  const r = loadReceiver();
  r.post(body([post('10', [
    comment('901'),
    comment('902', { parent_comment_id: '901', reply_comment_id: '902', is_reply: true, reply_target_id: '' }),
  ], { 'post content': 'PC' })]));
  const [top, reply] = r.ss.getSheetByName('Comments').records();
  assert.equal(top.is_reply, '');
  assert.equal(top.reply_target_id, '901');      // defaults to comment_id
  assert.equal(top['post content'], 'PC');       // inherited from the post
  assert.equal(top.post_id, '10');
  assert.equal(reply.parent_id, '901');          // parent_comment_id fallback
  assert.equal(reply.is_reply, 'yes');
  assert.equal(reply.reply_target_id, '902');
});

test('mapping unknown payload fields are ignored', () => {
  const r = loadReceiver();
  r.post(body([post('10', [comment('901', { aria_label: 'x', secret: 'y' })])]));
  const row = r.ss.getSheetByName('Comments').records()[0];
  assert.equal('aria_label' in row, false);
  assert.equal('secret' in row, false);
});

// ------------------------------------------------------------ identifiers

test('ids: long numeric strings stay exact text', () => {
  const r = loadReceiver();
  const big = '1234567890123456789';
  r.post(body([post('9876543210987654321', [comment(big, { author_id: '100012345678901234' })])]));
  const row = r.ss.getSheetByName('Comments').records()[0];
  assert.equal(row.comment_id, big);
  assert.equal(row.post_id, '9876543210987654321');
  assert.equal(row.author_id, '100012345678901234');
});

test('ids: leading-apostrophe and whitespace are normalized', () => {
  const r = loadReceiver();
  r.post(body([post("'10 ", [comment(" '901")])]));
  // idText_ strips only a leading apostrophe after String(); " '901" keeps its apostrophe after trim
  assert.equal(r.ss.getSheetByName('Posts').records()[0].post_id, '10');
  assert.equal(r.ss.getSheetByName('Comments').records()[0].comment_id, "'901");
});

test('ids: idText_ number formatting', () => {
  const r = loadReceiver();
  assert.equal(r.call('idText_', 1234567890123456), '1234567890123456');
  assert.equal(r.call('idText_', 0), '0');
  assert.equal(r.call('idText_', ''), '');
  assert.equal(r.call('idText_', null), '');
  assert.equal(r.call('idText_', undefined), '');
});

test('ids: numbers above 2^53 already lost precision', () => {
  // Characterization: if an ID ever reaches the script as a JS number it is irrecoverably rounded.
  // Python sends strings, so this only bites ids that Sheets itself coerced to numbers.
  assert.equal(loadReceiver().call('idText_', 12345678901234567890), '12345678901234567000');
});

test('ids: id column without text format coerces and loses precision', () => {
  // Negative control for the fake: proves setNumberFormat('@') is what protects ids.
  const r = loadReceiver();
  const sh = r.ss.insertSheet('X');
  sh.getRange(1, 1, 1, 1).setValues([['1234567890123456789']]);
  assert.equal(typeof sh.cell(1, 1), 'number');
});

test('ids: existing numeric id rows still match on upsert', () => {
  const r = loadReceiver();
  r.call('setup');
  const cs = r.ss.getSheetByName('Comments');
  cs.data.push([901, '10']);                      // legacy row stored as a Number
  const out = r.post(body([post('10', [comment('901')])]));
  assert.equal(out.comments_updated, 1);
  assert.equal(cs.records().length, 1);
});

// ------------------------------------------------------------ reply preservation

test('preserve: blank incoming reply keeps the existing reply and status', () => {
  const r = loadReceiver();
  r.post(body([post('10', [comment('901', { reply: 'ردي', reply_status: 'posted' })])]));
  r.post(body([post('10', [comment('901', { text: 'rescraped' })])]));
  const row = r.ss.getSheetByName('Comments').records()[0];
  assert.equal(row.reply, 'ردي');
  assert.equal(row.reply_status, 'posted');
  assert.equal(row.text, 'rescraped');
});

test('preserve: manual reply typed in the sheet survives a rescrape', () => {
  const r = loadReceiver();
  r.post(body([post('10', [comment('901')])]));
  const cs = r.ss.getSheetByName('Comments');
  const cols = r.call('headerMap_', cs);
  cs.getRange(2, cols.reply + 1).setValue('owner reply');
  r.post(body([post('10', [comment('901')])]));
  assert.equal(cs.records()[0].reply, 'owner reply');
  assert.equal(r.get({ action: 'pending_replies', token: TOKEN }).replies[0].reply, 'owner reply');
});

test('preserve: non-blank incoming reply overwrites', () => {
  // Characterization: the scraper always sends reply '' so this only happens for other senders.
  const r = loadReceiver();
  r.post(body([post('10', [comment('901', { reply: 'old' })])]));
  r.post(body([post('10', [comment('901', { reply: 'new' })])]));
  assert.equal(r.ss.getSheetByName('Comments').records()[0].reply, 'new');
});

test('preserve: received_at is refreshed on update', () => {
  const r = loadReceiver();
  r.post(body([post('10')]));
  const first = r.ss.getSheetByName('Posts').records()[0].received_at;
  r.post(body([post('10')]));
  assert.ok(r.ss.getSheetByName('Posts').records()[0].received_at >= first);
});

// ------------------------------------------------------------ mark_replied

test('mark_replied sets posted for known ids and counts them', () => {
  const r = seedForPending();
  assert.deepEqual(r.post({ token: TOKEN, action: 'mark_replied', comment_ids: ['901', '999', 905] }), { ok: true, marked: 2 });
  const rows = Object.fromEntries(r.ss.getSheetByName('Comments').records().map(x => [x.comment_id, x.reply_status]));
  assert.equal(rows['901'], 'posted');
  assert.equal(rows['905'], 'posted');
  assert.deepEqual(r.get({ action: 'pending_replies', token: TOKEN }).replies.map(x => x.comment_id), ['906']);
});

test('mark_replied with bad token changes nothing', () => {
  const r = seedForPending();
  assert.deepEqual(r.post({ token: 'x', action: 'mark_replied', comment_ids: ['901'] }), { ok: false, error: 'bad token' });
  assert.equal(r.get({ action: 'pending_replies', token: TOKEN }).count, 3);
});

test('mark_replied without ids marks nothing', () => {
  assert.deepEqual(seedForPending().post({ token: TOKEN, action: 'mark_replied' }), { ok: true, marked: 0 });
});

test('mark_replied when reply_status column missing marks nothing', () => {
  const r = loadReceiver();
  const cs = r.ss.insertSheet('Comments');
  cs.appendRow(['comment_id', 'reply']);
  cs.appendRow(['901', 'x']);
  assert.deepEqual(r.post({ token: TOKEN, action: 'mark_replied', comment_ids: ['901'] }), { ok: true, marked: 0 });
});

test('mark_replied does not touch Posts or dedupe', () => {
  const r = seedForPending();
  const posts = r.ss.getSheetByName('Posts').table();
  r.post({ token: TOKEN, action: 'mark_replied', comment_ids: ['901'] });
  assert.deepEqual(r.ss.getSheetByName('Posts').table(), posts);
});

// ------------------------------------------------------------ headers and dedupe

test('headers: missing columns are appended, existing order and extra columns kept', () => {
  const r = loadReceiver();
  const cs = r.ss.insertSheet('Comments');
  cs.appendRow(['comment_id', 'my_notes', 'text']);
  cs.appendRow(['901', 'note', 'old']);
  r.post(body([post('10', [comment('901', { text: 'new' })])]));
  const head = cs.table()[0];
  assert.deepEqual(head.slice(0, 3), ['comment_id', 'my_notes', 'text']);
  assert.equal(head.length, 3 + 15);
  const row = cs.records()[0];
  assert.equal(row.my_notes, '');          // characterization: upsert rewrites the whole row, clearing unknown columns
  assert.equal(row.text, 'new');
});

test('headers: blank header cell in the middle shifts headers left', () => {
  // Characterization (defect D-14): ensureHeaders_ filters out blank header cells, so headers to the
  // right of a gap move one column left while the data under them does not.
  const r = loadReceiver();
  const cs = r.ss.insertSheet('Comments');
  cs.appendRow(['comment_id', '', 'text']);
  cs.appendRow(['901', 'x', 'kept text']);
  r.call('ensureHeaders_', cs, [...r.consts.COMMENT_HEAD]);
  assert.deepEqual(cs.table()[0].slice(0, 2), ['comment_id', 'text']);
  assert.equal(cs.records()[0].text, 'x');
});

test('dedupe: pre-existing duplicate rows are removed keeping the first', () => {
  const r = loadReceiver();
  r.call('setup');
  const cs = r.ss.getSheetByName('Comments');
  cs.data.push(['901', 'first'], ['902', 'b'], ['901', 'dup'], ['901', 'dup2']);
  const out = r.post(body([]));
  assert.equal(out.duplicates_removed, 2);
  assert.deepEqual(cs.records().map(x => [x.comment_id, x.post_id]), [['901', 'first'], ['902', 'b']]);
  assert.deepEqual(cs.deleted, [5, 4]);     // bottom-up so indexes stay valid
});

test('dedupe: rows with blank id are left alone', () => {
  const r = loadReceiver();
  r.call('setup');
  const cs = r.ss.getSheetByName('Posts');
  cs.data.push(['', 'a'], ['', 'b']);
  assert.equal(r.call('dedupeSheet_', cs), 0);
});

test('dedupe: setup also dedupes', () => {
  const r = loadReceiver();
  r.call('setup');
  r.ss.getSheetByName('Posts').data.push(['1'], ['1']);
  r.call('setup');
  assert.equal(r.ss.getSheetByName('Posts').records().length, 1);
});

// ------------------------------------------------------------ helpers

test('helpers: findIdRow_ returns 1-based sheet row or 0', () => {
  const r = loadReceiver();
  r.post(body([post('10'), post('11')]));
  const ps = r.ss.getSheetByName('Posts');
  assert.equal(r.call('findIdRow_', ps, '11'), 3);
  assert.equal(r.call('findIdRow_', ps, '12'), 0);
});

test('helpers: upsert_ throws on missing id', () => {
  const r = loadReceiver();
  r.call('setup');
  const ps = r.ss.getSheetByName('Posts');
  assert.throws(() => r.call('upsert_', ps, '', [], r.call('headerMap_', ps)), /missing id/);
});

test('helpers: rowFrom_ places values by header and ignores unknown keys', () => {
  const r = loadReceiver();
  const row = r.call('rowFrom_', { a: 0, c: 2, _width: 3 }, { c: 'C', a: 'A', z: 'Z' });
  assert.deepEqual([...row], ['A', '', 'C']);
});

test('scale: 500 comments in one request', () => {
  const r = loadReceiver();
  const many = Array.from({ length: 500 }, (_, i) => comment(String(900000000000000 + i)));
  const out = r.post(body([post('10', many)]));
  assert.equal(out.comments_new, 500);
  assert.equal(r.ss.getSheetByName('Comments').records().length, 500);
});

test('scale: batches of the same post accumulate without duplicates', () => {
  const r = loadReceiver();
  const all = Array.from({ length: 10 }, (_, i) => comment(String(500 + i)));
  for (let i = 0; i < 10; i += 3) r.post(body([post('10', all.slice(i, i + 3))]));
  r.post(body([post('10', all.slice(0, 3))]));
  assert.equal(r.ss.getSheetByName('Comments').records().length, 10);
  assert.equal(r.ss.getSheetByName('Posts').records().length, 1);
});
