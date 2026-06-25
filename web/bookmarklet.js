/*
 * bookmarklet.js — readable source for the gdskill-match "upload full official data"
 * bookmarklet (spec §2, §4; plan Task 8).
 *
 * Runs in the player's ALREADY-AUTHENTICATED e-amusement tab. It does a cheap 37-page
 * rank sweep (music.html?gtype=dm&cat=0..36), a targeted set of music_detail fetches
 * (exact 達成率), normalises everything into a schema-v1 payload, and uploads it
 * (same-origin via the app upload page, or a download-JSON fallback).
 *
 * Design constraints honoured here:
 *   - SERIAL, no parallelism: category pages 1–1.5s apart; detail pages 2–3s + jitter;
 *     exponential back-off and STOP on repeated 429/5xx (spec §4.2). Keep KONAMI load low.
 *   - Never store/transmit カードナンバー / cookies / raw HTML: buildPayload() copies only a
 *     whitelist of profile fields, and only normalised chart metrics travel (spec §5.3, §8).
 *   - skill = level * 20 * achievement; achievement is a fraction 0..1 (spec §2).
 *   - Chart identity = (name, diff); diff ∈ {BAS,ADV,EXT,MAS}; sid is the stable eagate song id.
 *   - No eval / no innerHTML injection: parsing is pure text/regex extraction over the fetched
 *     HTML *string* (what fetch().then(r=>r.text()) yields) — the same code path the Node tests
 *     exercise, so there is no DOM dependency (see web/bookmarklet.test.mjs header).
 *
 * The parser is a set of PURE functions (parseCategory / parseDetail / rankFromClass /
 * parseAchievement / pickDetailQueue / buildPayload) exported for Node unit tests; the browser
 * runtime (`run`) is only defined/used when a real `document` + `fetch` exist.
 */
(function (root) {
  'use strict';

  // ---- shared payload contract (schema v1 — mirrors server/ingest.py) ---------
  var SCHEMA_VERSION = 1;
  var GAME = 'gitadora';
  var GTYPE = 'dm';
  var DEFAULT_VERSION = 'galaxywave_delta';
  var DEFAULT_DETAIL_CAP = 120;

  // Difficulty codes used in the payload (server DIFFS).
  var DIFF_ORDER = ['BAS', 'ADV', 'EXT', 'MAS'];
  // music_detail.html difficulty blocks -> payload diff codes.
  var DETAIL_DIFFS = [
    ['BAS', 'diff_BASIC'],
    ['ADV', 'diff_ADVANCED'],
    ['EXT', 'diff_EXTREME'],
    ['MAS', 'diff_MASTER'],
  ];
  var RANK_SET = { SS: 1, S: 1, A: 1, B: 1, C: 1, D: 1, E: 1 };
  // Profile keys allowed to leave the browser. カードナンバー / card / cookie are NEVER copied.
  var PROFILE_WHITELIST = ['gitadoraId', 'playerName', 'drumSkillPoint', 'allSongSkill'];

  // ---- tiny string helpers (pure) ---------------------------------------------

  function stripTags(html) {
    return String(html == null ? '' : html).replace(/<[^>]*>/g, '');
  }

  function decodeEntities(s) {
    return String(s)
      .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, ' ');
  }

  function textOf(html) {
    return decodeEntities(stripTags(html)).replace(/\s+/g, ' ').trim();
  }

  function parseIntOrNull(text) {
    var t = textOf(text).replace(/,/g, '');
    if (!/^-?\d+$/.test(t)) return null;
    var n = parseInt(t, 10);
    return isNaN(n) ? null : n;
  }

  function parseLevel(text) {
    var m = textOf(text).match(/(\d+(?:\.\d+)?)/);
    return m ? parseFloat(m[1]) : null;
  }

  /**
   * parseAchievement("65.15%") -> 0.6515 ; "-" / "" / null -> null.
   * The exact 達成率 is the basis for skill = level * 20 * achievement.
   */
  function parseAchievement(text) {
    if (text == null) return null;
    var m = textOf(text).match(/^(\d+(?:\.\d+)?)\s*%$/);
    if (!m) return null;
    return parseFloat(m[1]) / 100.0;
  }

  /**
   * rankFromClass(className) -> one of {SS,S,A,B,C,D,E,"-"} or null.
   * Handles BOTH the list-page medal class `score_data_rank icon<R>` (icon- = unplayed)
   * and the detail-page class `score_data_rank <R>` (none = unranked). Ignores layout tokens.
   */
  function rankFromClass(className) {
    if (!className) return null;
    var toks = String(className).split(/\s+/);
    for (var i = 0; i < toks.length; i++) {
      var t = toks[i];
      if (!t || t === 'score_data_rank') continue;
      var v = t.indexOf('icon') === 0 ? t.slice(4) : t;
      if (v === '-' || v === 'none') return '-';
      var u = v.toUpperCase();
      if (RANK_SET[u]) return u;
    }
    return null;
  }

  // Pull the class attribute out of an HTML fragment that holds a score_data_rank element.
  function rankFromFragment(html) {
    var m = String(html == null ? '' : html).match(/class="([^"]*score_data_rank[^"]*)"/i);
    if (!m) return '-';
    var r = rankFromClass(m[1]);
    return r == null ? '-' : r;
  }

  // ---- parseCategory: music.html?gtype=dm&cat=N (list page) -------------------

  /**
   * parseCategory(html) -> [{sid, name, ranks:{BAS,ADV,EXT,MAS}}].
   * One row per song; each of the 4 difficulty cells carries a single best-rank medal as a
   * CSS class. Header rows (no music_detail link) are skipped. `html` is the fetched page text.
   */
  function parseCategory(html) {
    var rows = [];
    var src = String(html == null ? '' : html);
    var trRe = /<tr\b[^>]*>([\s\S]*?)<\/tr>/gi;
    var tr;
    while ((tr = trRe.exec(src)) !== null) {
      var row = tr[1];
      var link = row.match(
        /<a\b[^>]*href="[^"]*music_detail\.html\?[^"]*\bsid=(\d+)[^"]*"[^>]*>([\s\S]*?)<\/a>/i);
      if (!link) continue; // header / non-song row
      var sid = link[1];
      var name = textOf(link[2]);

      // The 4 rank medals appear in document order = BAS, ADV, EXT, MAS.
      var medals = [];
      var cellRe = /<td\b[^>]*class="([^"]*score_data_rank[^"]*)"[^>]*>/gi;
      var cell;
      while ((cell = cellRe.exec(row)) !== null) {
        var r = rankFromClass(cell[1]);
        medals.push(r == null ? '-' : r);
      }
      var ranks = {};
      for (var d = 0; d < DIFF_ORDER.length; d++) {
        ranks[DIFF_ORDER[d]] = d < medals.length ? medals[d] : '-';
      }
      rows.push({ sid: sid, name: name, ranks: ranks });
    }
    return rows;
  }

  // ---- parseDetail: music_detail.html?gtype=dm&sid=N --------------------------

  /**
   * parseDetail(html, sid) -> {sid, charts:[{diff, level, rank, achievement, exact,
   *   playCount, clearCount, hiScore, maxCombo}]}.
   * Four difficulty blocks (diff_BASIC/ADVANCED/EXTREME/MASTER), each with a level and a table
   * of プレー回数 / クリア回数 / 最高ランク / 達成率 / ハイスコア / MAX COMBO rows. The 達成率
   * row gives the exact achievement (`exact:true`); an unplayed diff has rank '-' and null
   * achievement. `sid` is supplied by the caller (it requested ?sid=N); the HTML body omits it.
   */
  function parseDetail(html, sid) {
    var src = String(html == null ? '' : html);

    // Locate each difficulty block by its marker class, in document order.
    var marks = [];
    for (var i = 0; i < DETAIL_DIFFS.length; i++) {
      var code = DETAIL_DIFFS[i][0];
      var cls = DETAIL_DIFFS[i][1];
      var m = src.match(new RegExp('class="' + cls + '"'));
      if (m) marks.push({ code: code, cls: cls, idx: m.index });
    }
    marks.sort(function (a, b) { return a.idx - b.idx; });

    var charts = [];
    for (var j = 0; j < marks.length; j++) {
      var start = marks[j].idx;
      var end = (j + 1 < marks.length) ? marks[j + 1].idx : src.length;
      var section = src.slice(start, end);

      // Level from the marker div text, e.g. `class="diff_MASTER">MASTER 9.40<`.
      var lvlMatch = section.match(new RegExp('class="' + marks[j].cls + '"[^>]*>([^<]*)<'));
      var level = lvlMatch ? parseLevel(lvlMatch[1]) : null;

      var chart = {
        diff: marks[j].code, level: level, rank: '-', achievement: null, exact: false,
        playCount: null, clearCount: null, hiScore: null, maxCombo: null,
      };

      // idx_tb (label) / r (value) row pairs within this difficulty block.
      var rowRe =
        /<td\b[^>]*class="idx_tb[^"]*"[^>]*>([\s\S]*?)<\/td>\s*<td\b[^>]*class="r"[^>]*>([\s\S]*?)<\/td>/gi;
      var pair;
      while ((pair = rowRe.exec(section)) !== null) {
        var key = textOf(pair[1]);
        var val = pair[2];
        if (key === 'プレー回数') chart.playCount = parseIntOrNull(val);
        else if (key === 'クリア回数') chart.clearCount = parseIntOrNull(val);
        else if (key === '最高ランク') chart.rank = rankFromFragment(val);
        else if (key === '達成率') chart.achievement = parseAchievement(val);
        else if (key === 'ハイスコア') chart.hiScore = parseIntOrNull(val);
        else if (key === 'MAX COMBO') chart.maxCombo = parseIntOrNull(val);
      }
      chart.exact = chart.achievement != null;
      charts.push(chart);
    }
    return { sid: sid == null ? null : String(sid), charts: charts };
  }

  // ---- pickDetailQueue: which songs to detail-fetch (spec §4.3) ---------------

  function _rankScore(rank) {
    return { SS: 7, S: 6, A: 5, B: 4, C: 3, D: 2, E: 1 }[rank] || 0;
  }

  function _toSet(arr) {
    var s = {};
    (arr || []).forEach(function (x) { s[String(x)] = 1; });
    return s;
  }

  /**
   * pickDetailQueue(catRows, spec) -> [sid].
   * Only songs with at least one played difficulty (rank != '-') are candidates. They are
   * scored by best (rank × difficulty) potential, with large boosts for identity-challenge /
   * recommended / kasegi songs (spec §4.3), then capped at `spec.detailCap` (default 120).
   */
  function pickDetailQueue(catRows, spec) {
    spec = spec || {};
    var cap = spec.detailCap || DEFAULT_DETAIL_CAP;
    var challenge = _toSet(spec.challengeSids);
    var recommend = _toSet(spec.recommendSids);
    var kasegi = _toSet(spec.kasegiSids);
    var diffWeight = { BAS: 1.0, ADV: 1.5, EXT: 2.0, MAS: 2.5 };

    var scored = [];
    for (var i = 0; i < (catRows || []).length; i++) {
      var row = catRows[i];
      var best = 0;
      var played = false;
      for (var d = 0; d < DIFF_ORDER.length; d++) {
        var diff = DIFF_ORDER[d];
        var rank = row.ranks ? row.ranks[diff] : '-';
        if (rank && rank !== '-') {
          played = true;
          var s = _rankScore(rank) * diffWeight[diff];
          if (s > best) best = s;
        }
      }
      if (!played) continue; // spec §4.3: rank != '-' required
      var score = best;
      if (challenge[row.sid]) score += 1000;   // identity verification — always fetch
      if (recommend[row.sid]) score += 500;
      if (kasegi[row.sid]) score += 300;
      scored.push({ sid: row.sid, score: score, order: i });
    }
    // Highest priority first; stable on original order for ties.
    scored.sort(function (a, b) { return (b.score - a.score) || (a.order - b.order); });
    return scored.slice(0, cap).map(function (x) { return x.sid; });
  }

  // ---- buildPayload: schema-v1 payload from scraped data ----------------------

  function _catalogLevel(spec, sid, diff) {
    if (!spec) return null;
    if (spec.catalog && spec.catalog[sid] && spec.catalog[sid].levels) {
      var v = spec.catalog[sid].levels[diff];
      if (typeof v === 'number') return v;
    }
    if (spec.levels) {
      var w = spec.levels[sid + '|' + diff];
      if (typeof w === 'number') return w;
    }
    return null;
  }

  function _safeProfile(profile) {
    var out = {};
    profile = profile || {};
    for (var i = 0; i < PROFILE_WHITELIST.length; i++) {
      var k = PROFILE_WHITELIST[i];
      if (profile[k] !== undefined && profile[k] !== null) out[k] = profile[k];
    }
    return out; // カードナンバー / card / cookie deliberately never copied
  }

  /**
   * buildPayload(profile, scraped, spec) -> schema-v1 payload object.
   *   scraped = { catRows:[...parseCategory...], details:{ <sid>: parseDetail }, scrapedAt }.
   * For each (sid, diff) it prefers exact detail data (achievement -> exact:true), else emits a
   * rank-only row IF a level is known (from the detail block or the spec catalog) — a row with
   * no level is dropped because the server's validate() requires level ∈ [0,10]. Unplayed
   * difficulties ('-') are never emitted. Only whitelisted profile fields travel.
   */
  function buildPayload(profile, scraped, spec) {
    scraped = scraped || {};
    spec = spec || {};
    var catRows = scraped.catRows || [];
    var details = scraped.details || {};

    var charts = [];
    for (var i = 0; i < catRows.length; i++) {
      var row = catRows[i];
      var sid = String(row.sid);
      var det = details[sid] || details[row.sid];
      var detByDiff = {};
      if (det && det.charts) {
        det.charts.forEach(function (c) { detByDiff[c.diff] = c; });
      }
      for (var d = 0; d < DIFF_ORDER.length; d++) {
        var diff = DIFF_ORDER[d];
        var listRank = (row.ranks && row.ranks[diff]) || '-';
        var dc = detByDiff[diff];
        var detPlayed = dc && (dc.rank && dc.rank !== '-' ||
          dc.achievement != null || (dc.playCount || 0) > 0);

        var rank, achievement, exact, level, rec = null;
        if (detPlayed) {
          rank = (dc.rank && dc.rank !== '-') ? dc.rank : listRank;
          achievement = dc.achievement;
          exact = dc.achievement != null;
          level = (typeof dc.level === 'number') ? dc.level : _catalogLevel(spec, sid, diff);
          rec = dc;
        } else if (listRank && listRank !== '-') {
          rank = listRank;
          achievement = null;
          exact = false;
          level = _catalogLevel(spec, sid, diff);
        } else {
          continue; // unplayed -> not an observation
        }
        if (typeof level !== 'number') continue;          // no level -> cannot validate; drop
        if (!rank || rank === '-') continue;

        var chart = {
          sid: sid, name: row.name, diff: diff, rank: rank,
          achievement: achievement, exact: !!exact, level: level,
        };
        if (rec) {
          if (rec.playCount != null) chart.playCount = rec.playCount;
          if (rec.clearCount != null) chart.clearCount = rec.clearCount;
          if (rec.hiScore != null) chart.hiScore = rec.hiScore;
          if (rec.maxCombo != null) chart.maxCombo = rec.maxCombo;
        }
        charts.push(chart);
      }
    }

    var payload = {
      schema: SCHEMA_VERSION,
      version: spec.version || DEFAULT_VERSION,
      game: GAME,
      gtype: GTYPE,
      gsvPlayerId: spec.gsvPlayerId,
      scrapedAt: scraped.scrapedAt || null,
      mode: spec.mode || 'standard',
      profile: _safeProfile(profile),
      charts: charts,
    };
    if (spec.uploadToken) payload.uploadToken = spec.uploadToken;
    return payload;
  }

  /**
   * isUnavailablePage(html) -> true when the fetched page is an e-amusement
   * maintenance ("メンテナンス") or login-required ("ログインが必要") shell rather
   * than real play data. Used to abort the sweep immediately so we never hammer
   * KONAMI with 37 requests during maintenance nor upload an empty payload.
   */
  function isUnavailablePage(html) {
    return /メンテナンス|ログインが必要/.test(String(html == null ? '' : html));
  }

  // ---- browser runtime: rank sweep + targeted detail + upload -----------------
  // Only meaningful inside the authenticated eagate tab; guarded so Node never runs it.

  function _baseUrl(spec) {
    if (spec && spec.baseUrl) return spec.baseUrl;
    var version = (spec && spec.version) || DEFAULT_VERSION;
    return '/game/gfdm/gitadora_' + version + '/p/playdata/';
  }

  function _sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function _jitter(lo, hi) {
    return lo + Math.random() * (hi - lo);
  }

  // localStorage cache keyed by {version, sid} — rank snapshot + detail + fetchedAt (spec §4.2).
  var CACHE_TTL_MS = 14 * 24 * 60 * 60 * 1000; // 14 days

  function _cacheKey(version, sid) {
    return 'gdskill:' + version + ':' + sid;
  }

  function _cacheGet(version, sid) {
    try {
      var raw = root.localStorage && root.localStorage.getItem(_cacheKey(version, sid));
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function _cacheSet(version, sid, entry) {
    try {
      if (root.localStorage) {
        root.localStorage.setItem(_cacheKey(version, sid), JSON.stringify(entry));
      }
    } catch (e) { /* quota / privacy mode: skip caching */ }
  }

  // Re-fetch detail only when stale: missing, rank changed, expired, or newly recommended.
  function _needsDetail(cached, row, spec) {
    if (!cached || !cached.detail) return true;
    if (JSON.stringify(cached.ranks || {}) !== JSON.stringify(row.ranks || {})) return true;
    if (!cached.fetchedAt || (Date.now() - cached.fetchedAt) > CACHE_TTL_MS) return true;
    if (spec && spec.recommendSids && spec.recommendSids.map(String).indexOf(row.sid) >= 0 &&
        !cached.wasRecommended) return true;
    return false;
  }

  // Serial fetch with exponential back-off; returns text or null (stop-on-failure handled by caller).
  function _fetchText(url, attempt) {
    attempt = attempt || 0;
    return fetch(url, { credentials: 'include', redirect: 'follow' }).then(function (resp) {
      if (resp.status === 429 || resp.status >= 500) {
        if (attempt >= 3) return null;
        return _sleep(Math.pow(2, attempt) * 1500 + _jitter(0, 500))
          .then(function () { return _fetchText(url, attempt + 1); });
      }
      if (!resp.ok) return null;
      return resp.text();
    }).catch(function () {
      if (attempt >= 3) return null;
      return _sleep(Math.pow(2, attempt) * 1500).then(function () {
        return _fetchText(url, attempt + 1);
      });
    });
  }

  function _downloadJson(payload) {
    try {
      var blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      var url = URL.createObjectURL(blob);
      var a = root.document.createElement('a');
      a.href = url;
      a.download = 'gdskill-upload-' + (payload.gsvPlayerId || 'player') + '.json';
      root.document.body.appendChild(a);
      a.click();
      root.document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    } catch (e) { /* last-resort: caller already has the payload */ }
  }

  /**
   * run(spec) — browser-only orchestrator: cheap 37-page rank sweep, targeted detail fetches
   * (serial + throttled + cached), then POST to spec.uploadUrl (credentials omitted) with a
   * download-JSON fallback. Returns the payload it built.
   */
  function run(spec) {
    spec = spec || {};
    var version = spec.version || DEFAULT_VERSION;
    var base = _baseUrl(spec);
    var catRows = [];
    var bySid = {};
    var failures = 0;
    var aborted = false;   // set when a maintenance / login page is detected

    // 1) Rank sweep over cat=0..36 (serial, 1–1.5s apart).
    function sweep(cat) {
      if (cat > 36 || failures >= 3 || aborted) return Promise.resolve();
      var url = base + 'music.html?gtype=' + GTYPE + '&cat=' + cat;
      return _fetchText(url).then(function (html) {
        if (html == null) { failures += 1; }
        else if (isUnavailablePage(html) || (cat === 0 && parseCategory(html).length === 0)) {
          // Maintenance / not logged in: stop now — don't fetch the other 36 pages.
          aborted = true;
          return Promise.resolve();
        } else {
          failures = 0;
          parseCategory(html).forEach(function (r) {
            if (!bySid[r.sid]) { bySid[r.sid] = r; catRows.push(r); }
            else { bySid[r.sid].ranks = r.ranks; }
          });
        }
        if (aborted) return Promise.resolve();
        return _sleep(_jitter(1000, 1500)).then(function () { return sweep(cat + 1); });
      });
    }

    // 2) Targeted detail fetches (serial, 2–3s + jitter), honouring the localStorage cache.
    function fetchDetails() {
      var queue = pickDetailQueue(catRows, spec);
      var details = {};
      var idx = 0;
      failures = 0;
      function step() {
        if (idx >= queue.length || failures >= 3) return Promise.resolve(details);
        var sid = queue[idx++];
        var row = bySid[sid];
        var cached = _cacheGet(version, sid);
        if (!_needsDetail(cached, row, spec)) {
          details[sid] = cached.detail;
          return step(); // cache hit: no network, no throttle
        }
        var url = base + 'music_detail.html?gtype=' + GTYPE + '&sid=' + sid + '&index=&cat=&page=';
        return _fetchText(url).then(function (html) {
          if (html == null) { failures += 1; }
          else {
            failures = 0;
            var det = parseDetail(html, sid);
            details[sid] = det;
            _cacheSet(version, sid, {
              ranks: row ? row.ranks : null, detail: det, fetchedAt: Date.now(),
              wasRecommended: !!(spec.recommendSids &&
                spec.recommendSids.map(String).indexOf(sid) >= 0),
            });
          }
          return _sleep(_jitter(2000, 3000)).then(step);
        });
      }
      return step();
    }

    return sweep(0).then(function () {
      // Abort cleanly on a maintenance / login page: never upload an empty payload.
      if (aborted || catRows.length === 0) {
        return { error: 'unavailable',
                 reason: 'no song data found — e-amusement may be under maintenance '
                       + 'or you are not logged in. Log in, wait for the site to be up, '
                       + 'and run the bookmarklet again.' };
      }
      return fetchDetails();
    }).then(function (details) {
      if (details && details.error) return details;   // pass the abort result through
      var scraped = { catRows: catRows, details: details, scrapedAt: new Date().toISOString() };
      var payload = buildPayload(spec.profile || {}, scraped, spec);
      if (!spec.uploadUrl) { _downloadJson(payload); return payload; }
      return fetch(spec.uploadUrl, {
        method: 'POST',
        credentials: 'omit', // never send cookies to gdskill-match (spec §5.3)
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(function (resp) {
        if (!resp.ok && resp.status !== 202) _downloadJson(payload);
        return payload;
      }).catch(function () { _downloadJson(payload); return payload; });
    });
  }

  // ---- exports ----------------------------------------------------------------
  var api = {
    SCHEMA_VERSION: SCHEMA_VERSION,
    rankFromClass: rankFromClass,
    parseAchievement: parseAchievement,
    parseCategory: parseCategory,
    parseDetail: parseDetail,
    pickDetailQueue: pickDetailQueue,
    buildPayload: buildPayload,
    isUnavailablePage: isUnavailablePage,
    run: run,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;             // Node unit tests
  } else {
    root.GDSkillBookmarklet = api;    // browser global (bookmarklet entry: GDSkillBookmarklet.run(spec))
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
