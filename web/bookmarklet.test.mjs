// Node unit tests for the e-amusement scraper/parser (run: node --test web/bookmarklet.test.mjs).
//
// DEVIATION FROM PLAN (recorded): the plan's Task 8 sketch imports `jsdom` and passes a DOM
// `Document` to parseCategory/parseDetail. This repo deliberately ships NO JS build step and NO
// JS dependencies (no package.json; node_modules + package-lock.json are .gitignore'd). Adding
// jsdom would contradict that. Per the task's authorised fallback, the parser is refactored to
// expose PURE functions that operate on the fetched HTML *string* (exactly what `resp.text()`
// yields in the browser), so the same code path runs in the bookmarklet and under `node --test`
// with no DOM. Rank and 達成率 (achievement) extraction are still covered against the real
// sanitised fixtures.

import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import bm from './bookmarklet.js';

const catHtml = fs.readFileSync(
  new URL('../tests/fixtures/eagate/music_cat0.html', import.meta.url), 'utf8');
const detailHtml = fs.readFileSync(
  new URL('../tests/fixtures/eagate/music_detail_sid2.html', import.meta.url), 'utf8');

// ---- rankFromClass (list `icon<R>` + detail bare `<R>`/`none`) ----------------

test('rankFromClass reads list medal classes', () => {
  assert.strictEqual(bm.rankFromClass('zebra_black score_data_rank iconB'), 'B');
  assert.strictEqual(bm.rankFromClass('score_data_rank iconSS'), 'SS');
  assert.strictEqual(bm.rankFromClass('score_data_rank icon-'), '-');
  assert.strictEqual(bm.rankFromClass('zebra_black score_data_rank icon-'), '-');
});

test('rankFromClass reads detail rank classes', () => {
  assert.strictEqual(bm.rankFromClass('score_data_rank B'), 'B');
  assert.strictEqual(bm.rankFromClass('score_data_rank none'), '-');
  assert.strictEqual(bm.rankFromClass(''), null);
  assert.strictEqual(bm.rankFromClass(null), null);
});

// ---- parseAchievement ("65.15%" -> 0.6515) -----------------------------------

test('parseAchievement converts a percentage to a fraction', () => {
  assert.ok(Math.abs(bm.parseAchievement('65.15%') - 0.6515) < 1e-9);
  assert.ok(Math.abs(bm.parseAchievement('100.00%') - 1.0) < 1e-9);
  assert.strictEqual(bm.parseAchievement('-'), null);
  assert.strictEqual(bm.parseAchievement(''), null);
  assert.strictEqual(bm.parseAchievement(null), null);
});

// ---- parseCategory (list page: sids + per-difficulty medals) ------------------

test('parseCategory returns the right sids and rank medals', () => {
  const rows = bm.parseCategory(catHtml);
  const bySid = Object.fromEntries(rows.map((r) => [r.sid, r]));
  assert.deepStrictEqual(rows.map((r) => r.sid), ['1', '2', '5', '7']);

  // sid 1 ("0時20分のRoulette") is entirely unplayed.
  assert.deepStrictEqual(bySid['1'].ranks, { BAS: '-', ADV: '-', EXT: '-', MAS: '-' });
  // sid 2 ("10,000,000,000") has a B on MASTER only.
  assert.strictEqual(bySid['2'].name, '10,000,000,000');
  assert.strictEqual(bySid['2'].ranks.MAS, 'B');
  assert.strictEqual(bySid['2'].ranks.EXT, '-');
  // sid 5 has S on EXTREME; sid 7 has A on ADVANCE.
  assert.strictEqual(bySid['5'].ranks.EXT, 'S');
  assert.strictEqual(bySid['7'].ranks.ADV, 'A');
});

// ---- parseDetail (single song: 4 charts incl. exact 達成率) -------------------

test('parseDetail extracts exact achievement, rank, level and counts', () => {
  const r = bm.parseDetail(detailHtml, '2');
  assert.strictEqual(r.sid, '2');
  const mas = r.charts.find((c) => c.diff === 'MAS');
  assert.ok(Math.abs(mas.achievement - 0.6515) < 1e-4);
  assert.strictEqual(mas.exact, true);
  assert.strictEqual(mas.rank, 'B');
  assert.ok(Math.abs(mas.level - 9.4) < 1e-9);
  assert.strictEqual(mas.playCount, 3);
  assert.strictEqual(mas.clearCount, 1);
  assert.strictEqual(mas.hiScore, 1234567);
  assert.strictEqual(mas.maxCombo, 456);

  // Unplayed difficulties: achievement null, rank '-', not exact.
  const bas = r.charts.find((c) => c.diff === 'BAS');
  assert.strictEqual(bas.achievement, null);
  assert.strictEqual(bas.rank, '-');
  assert.strictEqual(bas.exact, false);
  assert.ok(Math.abs(bas.level - 4.45) < 1e-9);
});

// ---- pickDetailQueue (priority + cap; spec §4.3) ------------------------------

test('pickDetailQueue keeps played songs, drops unplayed, honours cap', () => {
  const rows = bm.parseCategory(catHtml);
  const q = bm.pickDetailQueue(rows, { detailCap: 10 });
  // sid 1 (all '-') must be excluded; the three played songs included.
  assert.ok(!q.includes('1'));
  assert.deepStrictEqual([...q].sort(), ['2', '5', '7']);
});

test('pickDetailQueue front-loads challenge songs and obeys the cap', () => {
  const rows = bm.parseCategory(catHtml);
  // sid 7 (A on ADV) would otherwise rank below sid 5 (S on EXT); a challenge boost lifts it.
  const q = bm.pickDetailQueue(rows, { detailCap: 1, challengeSids: ['7'] });
  assert.deepStrictEqual(q, ['7']);
});

// ---- buildPayload (schema v1; privacy) ---------------------------------------

test('buildPayload emits a schema-v1 payload from scraped data', () => {
  const rows = bm.parseCategory(catHtml);
  const details = { 2: bm.parseDetail(detailHtml, '2') };
  const profile = {
    gitadoraId: 'HG12B7108F', playerName: 'LASK',
    drumSkillPoint: 7530.40, allSongSkill: 24020.46,
    cardNumber: 'MUST-NOT-LEAK',          // privacy: must be stripped by buildPayload
  };
  const scraped = { catRows: rows, details, scrapedAt: '2026-06-26T00:00:00Z' };
  const spec = { version: 'galaxywave_delta', gsvPlayerId: 42, mode: 'standard',
                 uploadToken: 'tok123' };

  const p = bm.buildPayload(profile, scraped, spec);
  assert.strictEqual(p.schema, 1);
  assert.strictEqual(p.version, 'galaxywave_delta');
  assert.strictEqual(p.game, 'gitadora');
  assert.strictEqual(p.gtype, 'dm');
  assert.strictEqual(p.gsvPlayerId, 42);
  assert.strictEqual(p.mode, 'standard');
  assert.strictEqual(p.uploadToken, 'tok123');
  assert.strictEqual(p.scrapedAt, '2026-06-26T00:00:00Z');

  // Privacy: card number (and any forbidden key) never travels in the payload.
  assert.strictEqual(p.profile.cardNumber, undefined);
  assert.strictEqual(p.profile.playerName, 'LASK');
  assert.ok(Math.abs(p.profile.drumSkillPoint - 7530.40) < 1e-9);
  const flat = JSON.stringify(p).toLowerCase();
  assert.ok(!flat.includes('card'));
  assert.ok(!flat.includes('must-not-leak'));

  // The MASTER chart is exact (from the detail page); unplayed diffs are dropped.
  const masters = p.charts.filter((c) => c.sid === '2' && c.diff === 'MAS');
  assert.strictEqual(masters.length, 1);
  const m = masters[0];
  assert.strictEqual(m.exact, true);
  assert.ok(Math.abs(m.achievement - 0.6515) < 1e-4);
  assert.ok(Math.abs(m.level - 9.4) < 1e-9);
  assert.strictEqual(m.rank, 'B');
  // No unplayed ('-') rows leak into the payload.
  assert.ok(p.charts.every((c) => c.rank !== '-'));
  // Every emitted chart satisfies the server's validate() contract.
  for (const c of p.charts) {
    assert.ok(['SS', 'S', 'A', 'B', 'C', 'D', 'E'].includes(c.rank));
    assert.ok(['BAS', 'ADV', 'EXT', 'MAS'].includes(c.diff));
    assert.ok(typeof c.level === 'number' && c.level >= 0 && c.level <= 10);
    assert.ok(c.achievement === null || (c.achievement >= 0 && c.achievement <= 1));
  }
});

test('buildPayload attaches catalog levels to rank-only charts', () => {
  // A song played on EXTREME (S medal) but never detail-fetched: level must come
  // from the upload spec's catalog so the row still validates server-side.
  const rows = [{ sid: '5', name: '23 -twenty three-',
                  ranks: { BAS: '-', ADV: '-', EXT: 'S', MAS: '-' } }];
  const scraped = { catRows: rows, details: {}, scrapedAt: '2026-06-26T00:00:00Z' };
  const spec = { version: 'galaxywave_delta', gsvPlayerId: 7,
                 catalog: { 5: { levels: { EXT: 7.30 } } } };
  const p = bm.buildPayload({ playerName: 'X' }, scraped, spec);
  const ext = p.charts.find((c) => c.sid === '5' && c.diff === 'EXT');
  assert.ok(ext);
  assert.strictEqual(ext.exact, false);
  assert.strictEqual(ext.achievement, null);
  assert.strictEqual(ext.rank, 'S');
  assert.ok(Math.abs(ext.level - 7.30) < 1e-9);
  // Rank-only charts with no known level are dropped (cannot pass validate()).
  assert.ok(!p.charts.some((c) => c.level == null));
});

// ---- isUnavailablePage (maintenance / login guard, surfaced by live run) -------

test('isUnavailablePage detects maintenance and login shells', () => {
  assert.strictEqual(bm.isUnavailablePage('<div>ただいまメンテナンス中のためご利用いただけません</div>'), true);
  assert.strictEqual(bm.isUnavailablePage('<p>このページのご利用にはe-amusementへのログインが必要です</p>'), true);
  assert.strictEqual(bm.isUnavailablePage(catHtml), false);   // a real category page is fine
  assert.strictEqual(bm.isUnavailablePage(''), false);
});
