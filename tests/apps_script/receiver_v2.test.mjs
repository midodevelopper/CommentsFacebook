// Executable tests for the v2 receiver (apps_script/src/*.gs) against gas_fake_v2.mjs.
// Run alone:  node --test tests/apps_script/receiver_v2.test.mjs
// Each test name must be unique and single-quoted: tests/test_apps_script.py lists them by regex.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadV2, makeApp, seedV1, v1Post, v1Comment, KEYS, PROD, sha256 } from './gas_fake_v2.mjs';

const G = '1111111111111111';
const ME = '100000000000777';
const R = 'rehearsal-file-1';

function v1Fixture(ss, opts = {}) {
  const posts = [v1Post('201'), v1Post('202', { 'post content': 'https://example.org/x' }), v1Post('203', { author_id: '' })];
  const comments = [
    v1Comment('301', '201'),
    v1Comment('302', '201', { reply: 'hello', reply_status: 'posted' }),
    v1Comment('303', '201', { reply: 'draft', author_id: '100000000000002' }),
    v1Comment('304', '202', { parent_id: '301', reply_comment_id: '304', is_reply: 'yes' }),
    v1Comment('305', '202', { reply: 'no id draft', author_id: '', author_url: '' }),
  ];
  return seedV1(ss, Object.assign({ posts, comments, extraPostCol: 'owner_note', notes: [['Posts', 2, 11, 'note on content'], ['Comments', 3, 14, 'note on permalink']] }, opts));
}

function migrated(opts = {}) {
  const r = loadV2(opts);
  v1Fixture(r.ss, opts.seed);
  assert.equal(r.call('set_flags', { maintenance: true }, { target: PROD }).ok, true);
  const out = r.call('migrate_step', { run: 'run-test-1', group_names: { [G]: 'اسم الجروب' } }, { target: PROD });
  assert.equal(out.ok, true, JSON.stringify(out));
  assert.equal(out.result.complete, true);
  r.call('set_flags', { maintenance: false }, { target: PROD });
  return r;
}

function rehearsal(r, id = R) {
  const f = r.app.addFile(id, 'CF-rehearsal-test-' + id);
  v1Fixture(f);
  const a = r.call('allow_target', { target_id: id, mode: 'rw' }, { target: PROD });
  assert.equal(a.ok, true, JSON.stringify(a));
  return f;
}

function newPost(id, comments = [], extra = {}) {
  return Object.assign({ post_id: id, group_name: 'اسم', author: 'A', author_id: '100000000000099', text: 'p' + id, media_url: '',
    created_time: '2026-10-24T10:00:00+00:00', url: `https://www.facebook.com/groups/${G}/posts/${id}/`, comment_sort: 'الأحدث', comments }, extra);
}
function newComment(id, postId, extra = {}) {
  return Object.assign({ comment_id: id, parent_id: '', author: 'U', author_id: '100000000000005', text: 'c' + id,
    created_time: '2026-10-24T10:05:00+00:00', reply_comment_id: '', is_reply: false, reply_target_id: id, author_url: '',
    url: `https://www.facebook.com/groups/${G}/posts/${postId}/?comment_id=${id}` }, extra);
}
function rowOf(ss, tab, idCol, id) { return ss.getSheetByName(tab).records().find(r => String(r[idCol]) === id); }
function setCell(ss, tab, idCol, id, col, value) {
  const sh = ss.getSheetByName(tab);
  const head = sh.table()[0];
  const row = sh.table().findIndex(r => String(r[head.indexOf(idCol)]) === id) + 1;
  sh.getRange(row, head.indexOf(col) + 1).setValue(value);
  return row;
}

// ------------------------------------------------------------------ gates

test('v2 gate: v1 GET pending_replies is refused without touching tabs', () => {
  const r = loadV2();
  const out = r.get({ action: 'pending_replies', token: 'CommentSecrets' });
  assert.equal(out.ok, false);
  assert.equal(out.code, 'E_VERSION');
  assert.equal(r.calls.tabAccess, 0);
});

test('v2 gate: ping reports api version and flags', () => {
  const out = loadV2().get({});
  assert.equal(out.ok, true);
  assert.equal(out.api_version, 2);
  assert.equal(out.schema_version, '1');
});

test('v2 gate: v1 POST payload is refused before any tab access', () => {
  const r = loadV2();
  const out = r.post({ token: 'CommentSecrets', group_id: G, posts: [] });
  assert.equal(out.code, 'E_VERSION');
  assert.equal(r.calls.tabAccess, 0);
  assert.equal(r.post(null, '{not json').code, 'E_VERSION');
});

test('v2 gate: wrong key and ingest key on admin action are refused', () => {
  const r = loadV2();
  assert.equal(r.call('flags', {}, { target: PROD, key: 'nope' }).code, 'E_AUTH');
  assert.equal(r.call('flags', {}, { target: PROD, key: KEYS.ingest }).code, 'E_AUTH');
  assert.equal(r.calls.tabAccess, 0);
});

test('v2 gate: ingest refuses an explicit target and admin actions require one', () => {
  const r = migrated();
  assert.equal(r.ingest({ group_id: G, posts: [] }, { target: 'elsewhere' }).code, 'E_VALIDATION');
  assert.equal(r.call('flags', {}).code, 'E_VALIDATION');
  assert.equal(r.call('flags', {}, { target: 'unknown-file' }).code, 'E_TARGET_NOT_ALLOWED');
});

test('v2 gate: ingest on a v1 schema is refused and writes nothing', () => {
  const r = loadV2();
  v1Fixture(r.ss);
  const before = JSON.stringify(r.ss.getSheetByName('Posts').table());
  assert.equal(r.ingest({ group_id: G, posts: [newPost('900')] }).code, 'E_SCHEMA');
  assert.equal(JSON.stringify(r.ss.getSheetByName('Posts').table()), before);
});

test('v2 gate: maintenance blocks ingestion and kill switch blocks reply actions', () => {
  const r = migrated();
  r.call('set_flags', { maintenance: true }, { target: PROD });
  assert.equal(r.ingest({ group_id: G, posts: [newPost('900')] }).code, 'E_MAINTENANCE');
  r.call('set_flags', { maintenance: false, replies_kill: true }, { target: PROD });
  assert.equal(r.call('pending_replies', { account_id: ME }, { target: PROD }).code, 'E_KILLED');
  assert.equal(r.ingest({ group_id: G, posts: [newPost('900')] }).ok, true);
});

test('v2 gate: onEdit is inert while the schema is v1', () => {
  const r = loadV2();
  v1Fixture(r.ss);
  const cs = r.ss.getSheetByName('Comments');
  cs.getRange(2, 15).setValue('typed');
  r.context.onEdit({ range: cs.getRange(2, 15), source: r.ss });
  assert.equal(cs.getRange(2, 16).getValue(), '');
});

test('v2 gate: fault injection is refused on production', () => {
  const r = loadV2();
  v1Fixture(r.ss);
  const out = r.call('migrate_step', { run: 'run-test-1', fault: { after_step: 'M1:Posts', mode: 'throw' } }, { target: PROD });
  assert.equal(out.code, 'E_VALIDATION');
});

test('v2 allowlist: production can never be a rehearsal target and titles are checked', () => {
  const r = loadV2();
  assert.equal(r.call('allow_target', { target_id: PROD }, { target: PROD }).code, 'E_TARGET_NOT_ALLOWED');
  r.app.addFile('x1', 'random title');
  assert.equal(r.call('allow_target', { target_id: 'x1' }, { target: PROD }).code, 'E_TARGET_NOT_ALLOWED');
  r.app.addFile('b1', 'CF-backup-run');
  assert.equal(r.call('allow_target', { target_id: 'b1', mode: 'ro' }, { target: PROD }).ok, true);
  assert.equal(r.call('set_flags', { maintenance: true }, { target: 'b1' }).code, 'E_TARGET_NOT_ALLOWED');
  assert.equal(r.call('flags', {}, { target: 'b1' }).ok, true);
});

test('v2 allowlist: expired entries are refused', () => {
  const r = loadV2();
  rehearsal(r);
  const list = JSON.parse(r.app.props.getProperty('global.REHEARSAL_ALLOWLIST'));
  list[R].expires = Date.now() - 1;
  r.app.props.setProperty('global.REHEARSAL_ALLOWLIST', JSON.stringify(list));
  assert.equal(r.call('flags', {}, { target: R }).code, 'E_TARGET_NOT_ALLOWED');
});

// ------------------------------------------------------------------ migration

test('migration: v1 becomes v2 in place with gids, values, statuses and notes preserved', () => {
  const r = migrated();
  const ps = r.ss.getSheetByName('Posts'), cs = r.ss.getSheetByName('Comments');
  assert.equal(ps.getSheetId(), 318060874);
  assert.equal(cs.getSheetId(), 2107666404);
  assert.deepEqual(ps.table()[0], [...r.consts.POST_HEAD, 'owner_note']);
  assert.deepEqual(cs.table()[0], [...r.consts.COMMENT_HEAD]);
  const p1 = rowOf(r.ss, 'Posts', 'post_id', '201'), p2 = rowOf(r.ss, 'Posts', 'post_id', '202');
  assert.equal(p1.media_url, '');
  assert.equal(p2.media_url, 'https://example.org/x');
  assert.equal(p1.group_name, 'اسم الجروب');
  assert.ok(p1.created_time instanceof Date);
  assert.equal(p1.created_time.toISOString(), '2026-10-22T13:05:00.000Z');
  assert.equal(p1.received_at.toISOString(), '2026-10-22T13:20:11.000Z');
  const c = id => rowOf(r.ss, 'Comments', 'comment_id', id);
  assert.equal(c('302').reply_status, 'sent');
  assert.equal(c('303').reply_status, 'held');
  assert.equal(c('305').reply_status, 'held');
  assert.equal(c('301').reply_status, '');
  assert.equal(c('304').is_reply, true);
  assert.equal(c('301').is_reply, false);
  assert.equal(c('301').post_text, 'caption 201');
  assert.equal(c('301').url, 'https://www.facebook.com/groups/1111111111111111/posts/201/?comment_id=301');
  assert.equal(ps.getRange(2, ps.table()[0].indexOf('media_url') + 1).getNotes()[0][0], 'note on content');
  assert.equal(cs.getRange(3, cs.table()[0].indexOf('url') + 1).getNotes()[0][0], 'note on permalink');
  assert.ok(r.ss.getSheetByName('Posts__v1_backup').isSheetHidden());
  assert.equal(r.ss.getSheets().filter(s => s.getName().startsWith('__v2_build')).length, 0);
});

test('migration: legacy events go to the ledger and mirrors before anything can be sent', () => {
  const r = migrated();
  const ledger = r.ss.getSheetByName('_replies').records();
  assert.deepEqual(ledger.map(e => e.event).sort(), ['held-legacy', 'held-legacy', 'legacy-sent']);
  const v = r.call('verify', { run: 'run-test-1', group_names: { [G]: 'اسم الجروب' } }, { target: PROD });
  assert.equal(v.result.ok, true, JSON.stringify(v.result.issues));
  assert.equal(v.result.counts.sent, 1);
  assert.equal(v.result.counts.held, 2);
});

test('migration: a second run with the same label changes nothing', () => {
  const r = migrated();
  const before = r.call('snapshot_hash', { tabs: ['Posts', 'Comments'] }, { target: PROD }).result.sha256;
  const again = r.call('migrate_step', { run: 'run-test-1' }, { target: PROD });
  assert.equal(again.result.complete, true);
  assert.equal(r.call('snapshot_hash', { tabs: ['Posts', 'Comments'] }, { target: PROD }).result.sha256, before);
});

test('migration: production must be in maintenance', () => {
  const r = loadV2();
  v1Fixture(r.ss);
  assert.equal(r.call('migrate_step', { run: 'run-test-1' }, { target: PROD }).code, 'E_MAINTENANCE');
});

test('migration: unknown legacy reply_status stops it', () => {
  const r = loadV2();
  seedV1(r.ss, { posts: [v1Post('201')], comments: [v1Comment('301', '201', { reply: 'x', reply_status: 'weird' })] });
  r.call('set_flags', { maintenance: true }, { target: PROD });
  const out = r.call('migrate_step', { run: 'run-test-1' }, { target: PROD });
  assert.equal(out.code, 'E_VALIDATION');
});

function cleanResult() {
  const r = loadV2();
  rehearsal(r);
  const out = r.call('migrate_step', { run: 'run-test-1', group_names: { [G]: 'اسم الجروب' } }, { target: R });
  assert.equal(out.ok, true, JSON.stringify(out));
  const f = r.app.files.get(R);
  return { posts: JSON.stringify(f.getSheetByName('Posts').table()), comments: JSON.stringify(f.getSheetByName('Comments').table()) };
}

for (const step of ['M1:Posts', 'M2:Posts', 'M3:Posts', 'M4:Posts', 'M1:Comments', 'M2:Comments', 'M3:Comments', 'M4:Comments', 'M3:Posts:partial', 'M3:Comments:partial']) {
  test(`migration resume: interrupted after ${step} on a fresh copy converges to the clean result`, () => {
    const want = cleanResult();
    const r = loadV2();
    rehearsal(r);
    const first = r.call('migrate_step', { run: 'run-test-1', group_names: { [G]: 'اسم الجروب' }, fault: { after_step: step, mode: 'throw' } }, { target: R });
    assert.equal(first.ok, false);
    const resumed = r.call('migrate_step', { run: 'run-test-1', group_names: { [G]: 'اسم الجروب' } }, { target: R });
    assert.equal(resumed.ok, true, JSON.stringify(resumed));
    const f = r.app.files.get(R);
    assert.equal(JSON.stringify(f.getSheetByName('Posts').table()), want.posts);
    assert.equal(JSON.stringify(f.getSheetByName('Comments').table()), want.comments);
    assert.equal(f.getSheets().filter(s => s.getName().startsWith('__v2_build')).length, 0);
  });
}

test('migration: lost response after success is replayed with the same request id', () => {
  const r = loadV2();
  rehearsal(r);
  const body = { api_version: 2, request_id: 'mig-1', action: 'migrate_step', target: R, auth: { kind: 'admin', key: KEYS.admin },
    payload: { run: 'run-test-1', fault: { after_step: 'migrate_step', mode: 'drop_response' } } };
  const first = r.post(body);
  assert.equal(first.ok, false);
  const retry = r.post(Object.assign({}, body, { payload: { run: 'run-test-1' } }));
  assert.equal(retry.ok, true);
  assert.equal(retry.replayed, true);
  assert.equal(r.app.files.get(R).getSheetByName('_replies').records().length, 3);
});

test('migration: a corrupted build is detected before anything is replaced', () => {
  const r = loadV2();
  rehearsal(r);
  const before = JSON.stringify(r.app.files.get(R).getSheetByName('Posts').table());
  const out = r.call('migrate_step', { run: 'run-test-1', fault: { after_step: 'M1:Posts', mode: 'corrupt' } }, { target: R });
  assert.equal(out.ok, false);
  assert.equal(out.code, 'E_VALIDATION');
  assert.equal(JSON.stringify(r.app.files.get(R).getSheetByName('Posts').table()), before);
});

test('rollback: restores v1 content in the same sheets and keeps every restriction', () => {
  const r = loadV2();
  const f = rehearsal(r);
  const v1 = JSON.stringify(f.getSheetByName('Comments').table());
  r.call('migrate_step', { run: 'run-test-1' }, { target: R });
  const back = r.call('rollback_step', { run: 'run-test-1' }, { target: R });
  assert.equal(back.ok, true, JSON.stringify(back));
  assert.equal(JSON.stringify(f.getSheetByName('Comments').table()), v1);
  assert.equal(f.getSheetByName('Comments').getSheetId(), 2107666404);
  assert.equal(r.call('flags', {}, { target: R }).result.schema_version, '1');
  assert.equal(f.getSheetByName('_replies').records().filter(e => e.event === 'held-legacy').length, 2);
});

test('rollback: repeating a completed rollback is a no-op', () => {
  const r = loadV2();
  const f = rehearsal(r);
  const v1 = JSON.stringify(f.getSheetByName('Posts').table());
  r.call('migrate_step', { run: 'run-test-1' }, { target: R });
  assert.equal(r.call('rollback_step', { run: 'run-test-1' }, { target: R }).ok, true);
  const again = r.call('rollback_step', { run: 'run-test-1' }, { target: R });
  assert.equal(again.ok, true, JSON.stringify(again));
  assert.equal(again.result.restored, 'already');
  assert.equal(JSON.stringify(f.getSheetByName('Posts').table()), v1);
});

test('rollback: partial converge during rollback is redone on resume', () => {
  const r = loadV2();
  const f = rehearsal(r);
  const v1 = JSON.stringify(f.getSheetByName('Posts').table());
  r.call('migrate_step', { run: 'run-test-1' }, { target: R });
  const broken = r.call('rollback_step', { run: 'run-test-1', fault: { after_step: 'R:Posts:partial' } }, { target: R });
  assert.equal(broken.ok, false);
  const resumed = r.call('rollback_step', { run: 'run-test-1' }, { target: R });
  assert.equal(resumed.ok, true, JSON.stringify(resumed));
  assert.equal(JSON.stringify(f.getSheetByName('Posts').table()), v1);
});

test('verify: a changed cell after migration is reported', () => {
  const r = migrated();
  setCell(r.ss, 'Comments', 'comment_id', '301', 'text', 'tampered');
  const v = r.call('verify', { run: 'run-test-1', group_names: { [G]: 'اسم الجروب' } }, { target: PROD });
  assert.equal(v.result.ok, false);
  assert.ok(v.result.issues.some(i => i.includes('text')));
});

test('isolation: rehearsal on another file leaves production cells and namespace untouched', () => {
  const r = loadV2();
  v1Fixture(r.ss);
  const prodBefore = r.call('snapshot_hash', {}, { target: PROD }).result.sha256;
  const nsBefore = r.call('namespace_dump', {}, { target: PROD }).result.sha256;
  rehearsal(r);
  r.call('migrate_step', { run: 'run-test-1' }, { target: R });
  r.call('ingest_as', { group_id: G, posts: [newPost('901', [newComment('951', '901')])] }, { target: R });
  assert.equal(r.call('snapshot_hash', {}, { target: PROD }).result.sha256, prodBefore);
  assert.equal(r.call('namespace_dump', {}, { target: PROD }).result.sha256, nsBefore);
  assert.equal(r.ss.getSheetByName('_replies'), null);
});

test('selftest: refuses production and reports the formula mode on a copy', () => {
  const r = loadV2();
  assert.equal(r.call('selftest', {}, { target: PROD }).code, 'E_TARGET_NOT_ALLOWED');
  rehearsal(r);
  const out = r.call('selftest', {}, { target: R });
  assert.equal(out.ok, true, JSON.stringify(out));
  assert.equal(out.result.formula_mode, 'literal');
  assert.equal(out.result.grid.wrote, true);
  assert.equal(r.app.files.get(R).getSheets().filter(s => s.getName().startsWith('__selftest')).length, 0);
});

test('selftest: when text cells still parse formulas the apostrophe mode is chosen', () => {
  const r = loadV2({ formulaMode: 'always' });
  rehearsal(r);
  assert.equal(r.call('selftest', {}, { target: R }).result.formula_mode, 'apostrophe');
});

// ------------------------------------------------------------------ ingestion

test('ingest: new rows get dates, first-seen time, checkbox and dropdown', () => {
  const r = migrated();
  const out = r.ingest({ group_id: G, posts: [newPost('901', [newComment('951', '901', { is_reply: true, parent_id: '301', reply_comment_id: '951' })])] });
  assert.equal(out.ok, true, JSON.stringify(out));
  assert.equal(out.result.posts_new, 1);
  const c = rowOf(r.ss, 'Comments', 'comment_id', '951');
  assert.ok(c.created_time instanceof Date);
  assert.ok(c.received_at instanceof Date);
  assert.equal(c.is_reply, true);
  assert.equal(c.post_text, 'p901');
  const cs = r.ss.getSheetByName('Comments');
  const row = cs.table().findIndex(x => String(x[0]) === '951') + 1;
  assert.equal(cs.getRange(row, cs.table()[0].indexOf('is_reply') + 1).getDataValidation().getCriteriaType(), 'CHECKBOX');
  assert.equal(cs.getRange(row, cs.table()[0].indexOf('reply_status') + 1).getDataValidation().getCriteriaType(), 'VALUE_IN_LIST');
});

test('ingest: reply fields, unknown keys, bad ids, mismatched urls and naive times are rejected', () => {
  const r = migrated();
  const bad = [
    newPost('901', [newComment('951', '901', { reply: 'x' })]),
    newPost('901', [newComment('951', '901', { reply_status: 'pending' })]),
    newPost('901', [], { extra: 1 }),
    newPost('9x1'),
    newPost('901', [], { url: `https://www.facebook.com/groups/${G}/posts/999/` }),
    newPost('901', [newComment('951', '902')]),
    newPost('901', [], { created_time: '2026-10-24 10:00' }),
    newPost('901', [newComment('951', '901', { is_reply: 'yes' })]),
  ];
  for (const post of bad) assert.equal(r.ingest({ group_id: G, posts: [post] }).code, 'E_VALIDATION', JSON.stringify(post).slice(0, 120));
  assert.equal(r.ingest({ group_id: G, posts: Array.from({ length: 26 }, (_, i) => newPost(String(90000 + i))) }).code, 'E_VALIDATION');
  assert.equal(rowOf(r.ss, 'Posts', 'post_id', '901'), undefined);
});

test('ingest: updates write only changed scraper cells and never reply, owner columns or received_at', () => {
  const r = migrated();
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('951', '901')])] });
  const cs = r.ss.getSheetByName('Comments');
  const head = cs.table()[0];
  const row = cs.table().findIndex(x => String(x[0]) === '951') + 1;
  cs.getRange(row, head.indexOf('reply') + 1).setValue('owner typed');
  cs.getRange(row, head.indexOf('reply_status') + 1).setValue('pending');
  const firstSeen = cs.getRange(row, head.indexOf('received_at') + 1).getValue();
  cs.getRange(1, head.length + 1).setValue('my_col');
  cs.getRange(row, head.length + 1).setValue('keep');
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('951', '901', { text: 'edited' })])] });
  const c = rowOf(r.ss, 'Comments', 'comment_id', '951');
  assert.equal(c.text, 'edited');
  assert.equal(c.reply, 'owner typed');
  assert.equal(c.reply_status, 'pending');
  assert.equal(c.my_col, 'keep');
  assert.equal(c.received_at.getTime(), firstSeen.getTime());
});

test('ingest: appending at the exact grid edge inserts rows first', () => {
  const r = migrated();
  const cs = r.ss.getSheetByName('Comments');
  cs.maxRows = cs.getLastRow();
  const out = r.ingest({ group_id: G, posts: [newPost('901', [newComment('951', '901'), newComment('952', '901')])] });
  assert.equal(out.ok, true, JSON.stringify(out));
  assert.ok(cs.getMaxRows() > cs.getLastRow());
  assert.ok(rowOf(r.ss, 'Comments', 'comment_id', '952'));
});

test('ingest: text starting with = stays text when cells would parse formulas', () => {
  const r = migrated({ formulaMode: 'always' });
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('951', '901', { text: '=IMPORTXML("x")' })])] });
  const cs = r.ss.getSheetByName('Comments');
  const row = cs.table().findIndex(x => String(x[0]) === '951') + 1;
  const col = cs.table()[0].indexOf('text') + 1;
  assert.equal(cs.getRange(row, col).getFormulas()[0][0], '');
  assert.equal(cs.getRange(row, col).getValue(), '=IMPORTXML("x")');
});

test('ingest: the same request id is replayed without duplicating rows', () => {
  const r = migrated();
  const a = r.ingest({ group_id: G, posts: [newPost('901')] }, { request_id: 'same' });
  const b = r.ingest({ group_id: G, posts: [newPost('901')] }, { request_id: 'same' });
  assert.equal(a.ok && b.ok, true);
  assert.equal(b.replayed, true);
  assert.equal(r.ss.getSheetByName('Posts').records().filter(p => String(p.post_id) === '901').length, 1);
});

// ------------------------------------------------------------------ replies

function withDraft(r, id = '951', author = '100000000000005') {
  r.ingest({ group_id: G, posts: [newPost('901', [newComment(id, '901', { author_id: author })])] });
  setCell(r.ss, 'Comments', 'comment_id', id, 'reply', 'shukran');
  return id;
}

test('replies: held and sent legacy rows are never eligible, a new draft becomes pending', () => {
  const r = migrated();
  withDraft(r);
  const out = r.call('pending_replies', { account_id: ME }, { target: PROD });
  assert.equal(out.ok, true, JSON.stringify(out));
  assert.deepEqual(out.result.eligible.map(e => e.id), ['951']);
  const reasons = Object.fromEntries(out.result.blocked.map(b => [b.id, b.reason]));
  assert.equal(reasons['303'], 'held');
  assert.equal(reasons['305'], 'held');
  assert.equal(reasons['302'], 'already-sent');
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'pending');
});

test('replies: a held row stays blocked even if its status cell is cleared or set to pending', () => {
  const r = migrated();
  setCell(r.ss, 'Comments', 'comment_id', '303', 'reply_status', '');
  assert.equal(r.call('claim', { kind: 'comment', id: '303', account_id: ME }, { target: PROD }).code, 'E_HELD');
  setCell(r.ss, 'Comments', 'comment_id', '303', 'reply_status', 'pending');
  assert.equal(r.call('claim', { kind: 'comment', id: '303', account_id: ME }, { target: PROD }).code, 'E_HELD');
});

test('replies: own comment, missing author id, nested target and post target are blocked', () => {
  const r = migrated();
  r.ingest({ group_id: G, posts: [newPost('901', [
    newComment('951', '901', { author_id: ME }), newComment('952', '901', { author_id: '' }),
    newComment('953', '901', { is_reply: true, parent_id: '951', reply_comment_id: '953' })])] });
  ['951', '952', '953'].forEach(id => setCell(r.ss, 'Comments', 'comment_id', id, 'reply', 'x'));
  setCell(r.ss, 'Posts', 'post_id', '901', 'reply', 'y');
  const reasons = Object.fromEntries(r.call('pending_replies', { account_id: ME }, { target: PROD }).result.blocked.map(b => [b.id, b.reason]));
  assert.equal(reasons['951'], 'own-comment');
  assert.equal(reasons['952'], 'no-author-id');
  assert.equal(reasons['953'], 'nested-target-disabled');
  assert.equal(reasons['901'], 'post-replies-disabled');
});

test('replies: claim, second claim conflicts, prepare and mark sent', () => {
  const r = migrated();
  withDraft(r);
  const c = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD });
  assert.equal(c.ok, true, JSON.stringify(c));
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'sending');
  assert.equal(r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).code, 'E_CONFLICT');
  const p = r.call('prepare_submit', { kind: 'comment', id: '951', claim_id: c.result.claim_id, fence: c.result.fence, binding: c.result.binding }, { target: PROD });
  assert.equal(p.result.authorized, true);
  assert.equal(r.call('mark_status', { kind: 'comment', id: '951', claim_id: c.result.claim_id, fence: c.result.fence, to: 'sent', expected_status: 'sending' }, { target: PROD }).code, 'E_VALIDATION');
  const m = r.call('mark_status', { kind: 'comment', id: '951', claim_id: c.result.claim_id, fence: c.result.fence, to: 'sent',
    expected_status: 'sending', sent_reply_id: '9876543210', sent_at: '2026-10-24T11:00:00Z' }, { target: PROD });
  assert.equal(m.ok, true, JSON.stringify(m));
  assert.equal(r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).result, undefined);
  assert.equal(r.call('pending_replies', { account_id: ME }, { target: PROD }).result.blocked.find(b => b.id === '951').reason, 'already-sent');
});

test('replies: prepare_submit refuses when the draft, author or url changed after the claim', () => {
  for (const [col, value] of [['reply', 'changed text'], ['author_id', '100000000000444'], ['url', `https://www.facebook.com/groups/${G}/posts/901/?comment_id=999`]]) {
    const r = migrated();
    withDraft(r);
    const c = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).result;
    setCell(r.ss, 'Comments', 'comment_id', '951', col, value);
    const p = r.call('prepare_submit', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, binding: c.binding }, { target: PROD });
    assert.equal(p.code, 'E_CONFLICT', col);
  }
});

test('replies: a stale runner after lease expiry cannot get submit authorization', () => {
  const r = migrated();
  withDraft(r);
  const c = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).result;
  const key = [...r.app.props.map.keys()].find(k => k.includes('LEASE.comment.951'));
  const lease = JSON.parse(r.app.props.getProperty(key));
  lease.until = Date.now() - 1;
  r.app.props.setProperty(key, JSON.stringify(lease));
  assert.equal(r.call('renew', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence }, { target: PROD }).code, 'E_CONFLICT');
  assert.equal(r.call('prepare_submit', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, binding: c.binding }, { target: PROD }).code, 'E_CONFLICT');
  r.call('pending_replies', { account_id: ME }, { target: PROD });
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'pending');
});

test('replies: an expired lease after submit authorization becomes uncertain, never pending', () => {
  const r = migrated();
  withDraft(r);
  const c = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).result;
  r.call('prepare_submit', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, binding: c.binding }, { target: PROD });
  const key = [...r.app.props.map.keys()].find(k => k.includes('LEASE.comment.951'));
  const lease = JSON.parse(r.app.props.getProperty(key));
  lease.until = Date.now() - 1;
  r.app.props.setProperty(key, JSON.stringify(lease));
  const out = r.call('pending_replies', { account_id: ME }, { target: PROD });
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'uncertain');
  assert.equal(out.result.blocked.find(b => b.id === '951').reason, 'uncertain-history');
});

test('replies: failed is refused after submit authorization, uncertain is accepted', () => {
  const r = migrated();
  withDraft(r);
  const c = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).result;
  r.call('prepare_submit', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, binding: c.binding }, { target: PROD });
  const base = { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, expected_status: 'sending' };
  assert.equal(r.call('mark_status', Object.assign({ to: 'failed' }, base), { target: PROD }).code, 'E_CONFLICT');
  assert.equal(r.call('mark_status', Object.assign({ to: 'uncertain' }, base), { target: PROD }).ok, true);
});

test('replies: release before submit returns pending, after submit returns uncertain', () => {
  const r = migrated();
  withDraft(r);
  let c = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).result;
  assert.equal(r.call('release_claim', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence }, { target: PROD }).result.status, 'pending');
  c = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).result;
  assert.equal(c.fence, 2);
  r.call('prepare_submit', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, binding: c.binding }, { target: PROD });
  assert.equal(r.call('release_claim', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence }, { target: PROD }).result.status, 'uncertain');
});

test('replies: declined after sent adds do-not-contact and stale updates cannot undo it', () => {
  const r = migrated();
  withDraft(r, '951', '100000000000005');
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('952', '901', { author_id: '100000000000005' })])] });
  setCell(r.ss, 'Comments', 'comment_id', '952', 'reply', 'second');
  const c = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).result;
  r.call('prepare_submit', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, binding: c.binding }, { target: PROD });
  r.call('mark_status', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, to: 'sent', expected_status: 'sending', sent_reply_id: '9876543210', sent_at: '2026-10-24T11:00:00Z' }, { target: PROD });
  assert.equal(r.call('mark_status', { kind: 'comment', id: '951', to: 'declined', expected_status: 'sent', note: 'تم الرفض' }, { target: PROD }).ok, true);
  assert.equal(r.call('claim', { kind: 'comment', id: '952', account_id: ME }, { target: PROD }).code, 'E_DNC');
  assert.equal(r.call('mark_status', { kind: 'comment', id: '951', to: 'sent', expected_status: 'sending', sent_reply_id: '1' }, { target: PROD }).code, 'E_CONFLICT');
});

test('replies: do-not-contact survives deleting the declined row', () => {
  const r = migrated();
  withDraft(r, '951', '100000000000005');
  r.call('pending_replies', { account_id: ME }, { target: PROD });
  const d = r.call('mark_status', { kind: 'comment', id: '951', to: 'declined', expected_status: 'pending' }, { target: PROD });
  assert.equal(d.ok, true, JSON.stringify(d));
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('952', '901', { author_id: '100000000000005' })])] });
  const cs = r.ss.getSheetByName('Comments');
  const row = cs.table().findIndex(x => String(x[0]) === '951') + 1;
  cs.getRange(row, 1, 1, cs.getLastColumn()).clearContent();
  setCell(r.ss, 'Comments', 'comment_id', '952', 'reply', 'x');
  assert.equal(r.call('claim', { kind: 'comment', id: '952', account_id: ME }, { target: PROD }).code, 'E_DNC');
});

test('replies: owner-selected declined is caught by the server rescan even without onEdit', () => {
  const r = migrated();
  withDraft(r, '951', '100000000000005');
  setCell(r.ss, 'Comments', 'comment_id', '951', 'reply_status', 'declined');
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('952', '901', { author_id: '100000000000005' })])] });
  setCell(r.ss, 'Comments', 'comment_id', '952', 'reply', 'x');
  const out = r.call('pending_replies', { account_id: ME }, { target: PROD });
  assert.equal(out.result.blocked.find(b => b.id === '952').reason, 'dnc');
});

test('replies: a tampered ledger stops every reply action', () => {
  const r = migrated();
  withDraft(r);
  const led = r.ss.getSheetByName('_replies');
  led.getRange(2, 3).setValue('edited');
  r.ss.getSheetByName('_replies').getRange(led.getLastRow(), 1, 1, led.getLastColumn()).clearContent();
  const out = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD });
  assert.equal(out.code, 'E_INTEGRITY');
  assert.equal(r.app.props.getProperty('global.REPLIES_KILL'), 'true');
});

test('replies: sweep detects an edited ledger row through the hash chain', () => {
  const r = migrated();
  r.ss.getSheetByName('_replies').getRange(2, 5).setValue('999');
  assert.equal(r.call('sweep_restrictions', {}, { target: PROD }).code, 'E_INTEGRITY');
});

test('replies: a ledger event whose mirror write failed still restricts and is repaired', () => {
  const r = migrated();
  const keys = [...r.app.props.map.keys()].filter(k => k.includes('.SET.HELD.'));
  keys.forEach(k => r.app.props.deleteProperty(k));
  assert.equal(r.call('claim', { kind: 'comment', id: '303', account_id: ME }, { target: PROD }).code, 'E_HELD');
  const s = r.call('sweep_restrictions', {}, { target: PROD });
  assert.equal(s.ok, true, JSON.stringify(s));
  assert.ok([...r.app.props.map.keys()].some(k => k.includes('.SET.HELD.')));
});

test('capacity: mirrors over 90 percent of budget refuse new claims', () => {
  const r = migrated();
  withDraft(r);
  const ns = [...r.app.props.map.keys()].find(k => k.includes('.SET.HELD.')).split('.')[1];
  for (let i = 0; i < 42; i++) r.app.props.setProperty(`ns.${ns}.SET.DNC.${i}`, 'x'.repeat(8000));
  r.app.props.setProperty(`ns.${ns}.SET.DNC.n`, '42');
  assert.equal(r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).code, 'E_CAPACITY');
});

test('capacity: the fake store enforces the 9 KB value and 500 KB store limits', () => {
  const r = loadV2();
  assert.throws(() => r.app.props.setProperty('k', 'x'.repeat(9300)));
  for (let i = 0; i < 63; i++) r.app.props.setProperty('f' + i, 'x'.repeat(8000));
  assert.throws(() => r.app.props.setProperty('g', 'x'.repeat(8000)));
});

test('capacity: 25000 ids shard below the value limit', () => {
  const r = migrated();
  const ns = [...r.app.props.map.keys()].find(k => k.includes('.SET.HELD.')).split('.')[1];
  const ctx = { ns: ns, warnings: [], ss: r.ss, req: {} };
  for (let i = 0; i < 25000; i++) r.context.mirrorAdd_(ctx, 'DNC', r.context.setKey_('author', String(100000000000000 + i)));
  const shards = [...r.app.props.map.entries()].filter(([k]) => k.includes('.SET.DNC.') && !k.endsWith('.n'));
  assert.ok(shards.length > 1);
  assert.ok(shards.every(([, v]) => v.length <= 8000));
  assert.equal(r.context.mirrorLoad_(Object.assign({}, ctx, { _mirrors: undefined }), 'DNC').size, 25000);
});

function sentFixture() {
  const r = migrated();
  withDraft(r, '951', '100000000000005');
  const c = r.call('claim', { kind: 'comment', id: '951', account_id: ME }, { target: PROD }).result;
  r.call('prepare_submit', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, binding: c.binding }, { target: PROD });
  r.call('mark_status', { kind: 'comment', id: '951', claim_id: c.claim_id, fence: c.fence, to: 'sent', expected_status: 'sending',
    sent_reply_id: '7000000000001', sent_at: '2026-10-24T11:00:00Z' }, { target: PROD });
  return r;
}

test('outcomes: a nested answer from the target author to our reply marks replied', () => {
  const r = sentFixture();
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('7000000000002', '901', { author_id: '100000000000005', is_reply: true,
    parent_id: '951', reply_comment_id: '7000000000002', nest_parent_id: '7000000000001', created_time: '2026-10-24T11:30:00+00:00' })])] });
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'replied');
});

test('outcomes: mention-only, another author, or an older comment does not mark replied', () => {
  const cases = [
    { author_id: '100000000000005', mentions: [ME], nest_parent_id: '' },
    { author_id: '100000000000006', nest_parent_id: '7000000000001' },
    { author_id: '100000000000005', nest_parent_id: '7000000000001', created_time: '2026-10-24T10:59:00+00:00' },
  ];
  for (const extra of cases) {
    const r = sentFixture();
    r.ingest({ group_id: G, posts: [newPost('901', [newComment('7000000000003', '901', Object.assign({ is_reply: true, parent_id: '951',
      reply_comment_id: '7000000000003', created_time: '2026-10-24T11:30:00+00:00' }, extra))])] });
    assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'sent', JSON.stringify(extra));
  }
});

test('outcomes: facebook rejection on our own reply marks declined, elsewhere it is ignored', () => {
  const r = sentFixture();
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('7000000000009', '901', { author_id: '100000000000006', moderation: 'declined' })])] });
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'sent');
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('7000000000001', '901', { author_id: ME, is_reply: true, parent_id: '951',
    reply_comment_id: '7000000000001', moderation: 'declined' })])] });
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'declined');
});

test('outcomes: ingestion cannot change the author or url of a target that was sent', () => {
  const r = sentFixture();
  const out = r.ingest({ group_id: G, posts: [newPost('901', [newComment('951', '901', { author_id: '100000000000444' })])] });
  assert.equal(out.result.conflicts, 1);
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').author_id, '100000000000005');
});

// ------------------------------------------------------------------ onEdit

function edit(r, tab, row, col, rows = 1, cols = 1, oldValue) {
  const sh = r.ss.getSheetByName(tab);
  r.context.onEdit({ range: sh.getRange(row, col, rows, cols), source: r.ss, oldValue: oldValue });
}

test('onEdit: a multi-row paste of replies sets pending only on unrestricted rows', () => {
  const r = migrated();
  r.ingest({ group_id: G, posts: [newPost('901', [newComment('951', '901'), newComment('952', '901')])] });
  const cs = r.ss.getSheetByName('Comments');
  const head = cs.table()[0];
  const rows = ['951', '952', '303'].map(id => cs.table().findIndex(x => String(x[0]) === id) + 1);
  rows.forEach(row => cs.getRange(row, head.indexOf('reply') + 1).setValue('pasted'));
  const first = Math.min(...rows), last = Math.max(...rows);
  edit(r, 'Comments', first, head.indexOf('reply') + 1, last - first + 1, 1);
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'pending');
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '952').reply_status, 'pending');
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '303').reply_status, 'held');
});

test('onEdit: setting pending on a held row is reverted and declined is permanent', () => {
  const r = migrated();
  const cs = r.ss.getSheetByName('Comments');
  const head = cs.table()[0];
  const row = setCell(r.ss, 'Comments', 'comment_id', '303', 'reply_status', 'pending');
  edit(r, 'Comments', row, head.indexOf('reply_status') + 1, 1, 1, 'held');
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '303').reply_status, 'held');
  withDraft(r);
  const row2 = setCell(r.ss, 'Comments', 'comment_id', '951', 'reply_status', 'declined');
  edit(r, 'Comments', row2, head.indexOf('reply_status') + 1, 1, 1, '');
  assert.equal(r.ss.getSheetByName('_replies').records().some(e => e.event === 'dnc-add'), true);
  setCell(r.ss, 'Comments', 'comment_id', '951', 'reply_status', 'pending');
  edit(r, 'Comments', row2, head.indexOf('reply_status') + 1, 1, 1, 'declined');
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'declined');
});

test('onEdit: clearing a pending draft clears its status', () => {
  const r = migrated();
  withDraft(r);
  const cs = r.ss.getSheetByName('Comments');
  const head = cs.table()[0];
  const row = cs.table().findIndex(x => String(x[0]) === '951') + 1;
  edit(r, 'Comments', row, head.indexOf('reply') + 1);
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, 'pending');
  cs.getRange(row, head.indexOf('reply') + 1).setValue('');
  edit(r, 'Comments', row, head.indexOf('reply') + 1);
  assert.equal(rowOf(r.ss, 'Comments', 'comment_id', '951').reply_status, '');
});

// ------------------------------------------------------------------ format, dashboard, inventory

test('format and dashboard: build is idempotent and wires slicers, pivots and charts to one source', () => {
  const r = migrated();
  assert.equal(r.call('format', {}, { target: PROD }).ok, true);
  const d1 = r.call('build_dashboard', {}, { target: PROD });
  assert.equal(d1.ok, true, JSON.stringify(d1));
  const d2 = r.call('build_dashboard', {}, { target: PROD });
  assert.equal(d2.ok, true);
  assert.equal(r.ss.getSheets().filter(s => s.getName() === 'Dashboard').length, 1);
  const dash = r.ss.getSheetByName('Dashboard');
  assert.equal(r.ss.getSheets()[0], dash);
  assert.equal(dash.getSlicers().length, 3);
  assert.equal(dash.pivots.length, 5);
  assert.equal(dash.getCharts().length, 5);
  assert.ok(dash.pivots.every(p => p.source.getA1Notation() === dash.slicers[0].range.getA1Notation()));
  assert.ok(r.ss.getSheetByName('_activity').isSheetHidden());
  assert.ok(r.ss.getSheetByName('_activity').getRange(1, 1).getFormulas()[0][0].includes('FILTER'));
});

test('inventory: reports grid, notes, received_at times and flags without writing', () => {
  const r = loadV2();
  v1Fixture(r.ss);
  const before = r.call('snapshot_hash', {}, { target: PROD }).result.sha256;
  const inv = r.call('inventory', {}, { target: PROD });
  assert.equal(inv.ok, true, JSON.stringify(inv));
  const comments = inv.result.sheets.find(s => s.name === 'Comments');
  assert.equal(comments.gid, 2107666404);
  assert.equal(comments.received_at.with_time, 5);
  assert.equal(inv.result.time_zone, 'Asia/Riyadh');
  assert.equal(r.call('snapshot_hash', {}, { target: PROD }).result.sha256, before);
});

test('delete_empty_tab: deletes only an empty non-data tab by gid', () => {
  const r = migrated();
  const empty = r.ss.insertSheet('الورقة1', undefined, { gid: 0 });
  r.ss.getSheetByName('Posts');
  assert.equal(r.call('delete_empty_tab', { gid: 318060874 }, { target: PROD }).code, 'E_VALIDATION');
  empty.getRange(5, 5).setValue('x');
  assert.equal(r.call('delete_empty_tab', { gid: 0 }, { target: PROD }).result.deleted, false);
  empty.getRange(5, 5).setValue('');
  assert.equal(r.call('delete_empty_tab', { gid: 0 }, { target: PROD }).result.deleted, true);
});

test('capabilities: all checks pass against an allowlisted copy', () => {
  const r = loadV2();
  rehearsal(r);
  const out = r.call('capabilities', {}, { target: R });
  assert.equal(out.result.ok, true, JSON.stringify(out.result.checks));
});

// ------------------------------------------------------------------ full-size synthetic snapshot (shape of the live Sheet, synthetic data)

test('scale: a synthetic sheet shaped like production migrates and verifies', () => {
  const r = loadV2();
  const posts = [], comments = [];
  for (let i = 0; i < 499; i++) {
    const id = String(5000000 + i);
    const kind = i < 310 ? 'copy' : i < 491 ? 'link' : 'empty';
    posts.push(v1Post(id, { group_id: String(1000000000000000 + (i % 30)), 'post content': kind === 'copy' ? 'caption ' + id : kind === 'link' ? 'https://www.facebook.com/x/' + i : '', author_id: i < 10 ? '' : '100000000000099' }));
  }
  for (let i = 0; i < 946; i++) {
    const pid = String(5000000 + (i % 499));
    const extra = { group_id: String(1000000000000000 + ((i % 499) % 30)), 'post content': posts[i % 499]['post content'] };
    if (i < 66) Object.assign(extra, { is_reply: 'yes', parent_id: String(8000000), reply_comment_id: String(9000000 + i) });
    if (i >= 100 && i < 121) Object.assign(extra, { reply: 'r' + i, reply_status: 'posted' });
    if (i >= 121 && i < 212) Object.assign(extra, { reply: 'd' + i });
    if (i >= 900) extra.author_id = '';
    comments.push(v1Comment(String(9000000 + i), pid, extra));
  }
  seedV1(r.ss, { posts, comments });
  r.ss.getSheetByName('Comments').maxRows = 947;
  r.call('set_flags', { maintenance: true }, { target: PROD });
  const t0 = Date.now();
  const out = r.call('migrate_step', { run: 'run-scale-1' }, { target: PROD });
  assert.equal(out.ok, true, JSON.stringify(out).slice(0, 300));
  const v = r.call('verify', { run: 'run-scale-1' }, { target: PROD });
  assert.equal(v.result.ok, true, JSON.stringify(v.result.issues));
  assert.equal(v.result.counts.Posts, 499);
  assert.equal(v.result.counts.Comments, 946);
  assert.equal(v.result.counts.sent, 21);
  assert.equal(v.result.counts.held, 91);
  assert.equal(v.result.counts.is_reply_true, 66);
  assert.equal(v.result.counts.media_url_blank, 318);
  assert.ok(Date.now() - t0 < 60000);
  r.call('set_flags', { maintenance: false }, { target: PROD });
  const ing = r.ingest({ group_id: G, posts: [newPost('901', [newComment('951', '901')])] });
  assert.equal(ing.ok, true, JSON.stringify(ing));
});
