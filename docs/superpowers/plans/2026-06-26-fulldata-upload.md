# Full-Data Upload & Overlay — Implementation Plan (MVP / Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is calibrated for execution by capable subagents that have read the spec; algorithmically-critical functions include full code, standard plumbing specifies exact behaviour + tests.

**Goal:** Let an opted-in DrumMania player scrape their full official play data via a browser bookmarklet and upload it, so the engine densifies that player's vector and produces more accurate, overlay-aware profile / similarity / song recommendations — private by default, one-click publish.

**Architecture:** A client-side bookmarklet (runs in the player's authenticated e-amusement tab) does a cheap 37-page rank sweep + targeted `music_detail` fetches, normalises to a versioned JSON payload, and uploads it (same-origin via the app's upload page, or download-then-upload fallback). A self-contained ingestion module validates + identity-links + stores to `userdata/*` (local FS or GCS). The read-only engine lazily loads a single player's overlay and applies a Bayesian rank→achievement prior and asymmetric similarity so the dense uploader is compared fairly against the still-sparse gsv population. Uploaded data never mutates `matrix.npz` or the base per-chart stats.

**Tech Stack:** Python 3.10+ stdlib (`http.server`, `urllib`, `json`), `numpy` (engine), `pytest` (tests); vanilla JS for the bookmarklet + SPA (no build step); Node for bookmarklet unit tests. GCS optional (mirrors existing `cloudstore` local/GCS duality).

## Global Constraints

- Engine/server depends only on stdlib + `numpy`/`scipy`/`scikit-learn` (already in `requirements.txt`); the HTTP server itself uses stdlib only. Do not add new runtime deps for the server. (verbatim from spec §1 / README)
- **Uploaded data走 overlay 疊加層；絕不寫進 `matrix.npz`、絕不重算 `chart_mean/chart_std/idf/SVD`。** (spec §3 core invariant)
- Never store/transmit カードナンバー or any raw HTML / cookie / auth material. (spec §5.3, §8)
- `skill = level × 20 × achievement`; achievement is a fraction 0..1. (spec §2)
- Chart identity = `(name, diff)`; diff ∈ {BAS, ADV, EXT, MAS}; `sid` is the stable eagate song id. (build_dataset.py / spec §2)
- KONAMI fetches: serial only, no parallelism; category pages 1–1.5s apart, detail pages 2–3s + jitter; back off and stop on 429/5xx. (spec §4.2)
- Daily base rebuild MUST NOT delete/overwrite `userdata/*`. (spec §5.4)
- Identity is self-attested (no server-side KONAMI auth); ambiguous → `unlinked`, never overwrite. (spec §8)
- Visibility: private by default; only `public` overlays may feed other players' similar/rivals. (spec §9)
- All new user-facing strings get keys in `web/i18n.js` for all 4 locales (zh-Hant / en / ja / ko), matching existing patterns. (README / web/i18n.js)
- Version is parameterised (`galaxywave_delta` default); do not hardcode elsewhere. (engine/app pattern)

**Out of scope for this plan (later phases):** direct cross-origin CORS POST from eagate (MVP uses same-origin upload page + download fallback); `pipeline/build_official_stats.py` global de-bias layer (spec §7, Phase 3); latent-SVD projection fix for overlay users (Phase 2 — MVP downweights latent for overlay users).

---

## File Structure

**Create:**
- `web/bookmarklet.js` — readable bookmarklet source (rank sweep, detail queue, throttle, cache, payload builder, download + POST).
- `web/bookmarklet.test.mjs` — Node unit tests for the parser/normaliser (pure functions exported under `globalThis`/module guard).
- `tests/fixtures/eagate/music_cat0.html`, `music_detail_sid2.html` — real sanitised HTML fixtures (cookies/query-strings stripped) for parser tests.
- `server/userstore.py` — read/write `userdata/{raw,latest,unlinked}` on local FS or GCS.
- `server/ingest.py` — payload validation, identity linking, store orchestration (transport-agnostic; returns `(status, body, headers)`).
- `server/ranks.py` — rank→[L,U] table + Bayesian combine of rank prior with context prior.
- `server/overlay.py` — load a player's latest overlay, build per-chart observation arrays for the engine.
- `upload_fn/main.py` — Cloud Function entrypoint that wraps `server/ingest.py` (thin).
- `upload_fn/requirements.txt` — `google-cloud-storage` (function-only dep; not the server's).
- `tests/test_ranks.py`, `tests/test_userstore.py`, `tests/test_ingest.py`, `tests/test_overlay.py`, `tests/test_engine_overlay.py`, `tests/test_app_upload.py`.

**Modify:**
- `server/engine.py` — overlay-aware `profile` / `similar_players` / `song_recs`; helpers for asymmetric similarity and overlay z-scores. Keep existing sparse behaviour when no overlay.
- `server/app.py` — optional `--enable-upload` flag mounting `POST /api/upload` (off by default for the read-only cloud service); inject overlay into player endpoints; expose `enhanced`/`visibility` in responses; CORS preflight handling for the upload route.
- `web/index.html`, `web/app.js`, `web/styles.css`, `web/i18n.js` — upload page/section, status, download/upload fallback, publish toggle, "enhanced" badges in profile/recs.
- `README.md` — short "上傳完整資料" section.

**Shared contract — payload schema v1** (single source of truth, copied verbatim into `server/ingest.py` as `SCHEMA_VERSION = 1` validation and mirrored in `web/bookmarklet.js`):

```json
{
  "schema": 1, "version": "galaxywave_delta", "game": "gitadora", "gtype": "dm",
  "gsvPlayerId": 12345, "uploadToken": "<optional one-time token>",
  "scrapedAt": "ISO-8601",
  "mode": "standard|rank-only|full",
  "profile": { "gitadoraId": "HG12B7108F", "playerName": "LASK",
               "drumSkillPoint": 7530.40, "allSongSkill": 24020.46 },
  "charts": [ { "sid": "2", "name": "10,000,000,000", "diff": "MAS",
                "rank": "B", "achievement": 0.6515, "exact": true,
                "level": 8.45, "playCount": 3, "clearCount": 1,
                "hiScore": 1234567, "maxCombo": 456 } ]
}
```
`rank ∈ {SS,S,A,B,C,D,E,"-"}`; `exact=true` ⇒ `achievement` is from a detail page; `exact=false` ⇒ rank-only (achievement may be null). diff ∈ {BAS,ADV,EXT,MAS}.

---

## Task 1: Rank→achievement model (`server/ranks.py`)

**Files:**
- Create: `server/ranks.py`
- Test: `tests/test_ranks.py`

**Interfaces:**
- Produces:
  - `RANK_BANDS: dict[str, tuple[float, float]]` — rank → `(L, U)` achievement fraction bounds.
  - `rank_prior(rank: str) -> tuple[float, float]` → `(mu_rank, var_rank)` with `mu=(L+U)/2`, `var=(U-L)**2/12`; raises `KeyError` on unknown rank; `"-"` → not callable (caller treats as unplayed).
  - `bayes_combine(mu_rank, var_rank, mu_ctx, var_ctx, band) -> tuple[float, float]` → `(mu_post, var_post)`; precision-weighted, `mu_post` clipped into `band=(L,U)`.
  - `obs_from_rank(rank, level, mu_ctx, var_ctx) -> dict` → `{mu_ach, var_ach, skill_mean, skill_sd, weight}` where `skill_mean=level*20*mu_post`, `skill_sd=level*20*sqrt(var_post)`, `weight∈[0.15,0.65]` scaled by band width (narrower band → higher weight).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ranks.py
import math
import pytest
from server import ranks

def test_bands_cover_and_order():
    bands = ranks.RANK_BANDS
    for r in ["SS","S","A","B","C","D","E"]:
        L,U = bands[r]
        assert 0.0 <= L < U <= 1.0
    # anchor observed live: 65.15% => rank B  (spec §2)
    L,U = bands["B"]
    assert L <= 0.6515 <= U

def test_rank_prior_mean_var():
    L,U = ranks.RANK_BANDS["S"]
    mu,var = ranks.rank_prior("S")
    assert mu == pytest.approx((L+U)/2)
    assert var == pytest.approx((U-L)**2/12)

def test_bayes_clipped_into_band():
    band = ranks.RANK_BANDS["B"]
    # ctx pulls far above the band; result must stay inside band
    mu_post, var_post = ranks.bayes_combine(*ranks.rank_prior("B"), mu_ctx=0.99, var_ctx=0.0001, band=band)
    assert band[0] <= mu_post <= band[1]
    assert var_post > 0

def test_obs_from_rank_skill_and_weight():
    obs = ranks.obs_from_rank("S", level=8.0, mu_ctx=0.92, var_ctx=0.01)
    assert obs["skill_mean"] == pytest.approx(8.0*20*obs["mu_ach"])
    assert 0.15 <= obs["weight"] <= 0.65
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_ranks.py -v` → FAIL (module/attrs missing).

- [ ] **Step 3: Implement `server/ranks.py`**

```python
"""Rank → achievement modelling. A rank medal is an INTERVAL observation, not
an exact %. We combine the uniform rank prior with the engine's context prior
(same-bracket holders + kasegi + chart mean) by precision, clipped to the band."""
import math

# Achievement fraction bounds per GITADORA DrumMania best-rank medal.
# Anchored by a live observation (65.15% => B); refine during validation.
RANK_BANDS = {
    "SS": (0.95, 1.00), "S": (0.90, 0.95), "A": (0.80, 0.90),
    "B": (0.65, 0.80),  "C": (0.50, 0.65), "D": (0.30, 0.50),
    "E": (0.00, 0.30),
}

def rank_prior(rank):
    L, U = RANK_BANDS[rank]
    mu = (L + U) / 2.0
    var = (U - L) ** 2 / 12.0
    return mu, var

def bayes_combine(mu_rank, var_rank, mu_ctx, var_ctx, band):
    tau_rank = 1.0 / max(var_rank, 1e-9)
    tau_ctx = 1.0 / max(var_ctx, 1e-9)
    mu_post = (tau_rank * mu_rank + tau_ctx * mu_ctx) / (tau_rank + tau_ctx)
    var_post = 1.0 / (tau_rank + tau_ctx)
    L, U = band
    return min(max(mu_post, L), U), var_post

def obs_from_rank(rank, level, mu_ctx, var_ctx):
    band = RANK_BANDS[rank]
    mu_r, var_r = rank_prior(rank)
    mu_post, var_post = bayes_combine(mu_r, var_r, mu_ctx, var_ctx, band)
    width = band[1] - band[0]            # narrower band => more confident
    weight = 0.15 + 0.50 * (1.0 - min(width / 0.30, 1.0))
    return {
        "mu_ach": mu_post, "var_ach": var_post,
        "skill_mean": level * 20.0 * mu_post,
        "skill_sd": level * 20.0 * math.sqrt(var_post),
        "weight": round(weight, 4),
    }
```

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_ranks.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add server/ranks.py tests/test_ranks.py && git commit -m "feat(engine): Bayesian rank→achievement interval prior"`

---

## Task 2: User overlay store (`server/userstore.py`)

**Files:**
- Create: `server/userstore.py`
- Test: `tests/test_userstore.py`

**Interfaces:**
- Produces:
  - `save_upload(version, gsv_player_id, upload_id, payload: dict) -> None` — writes immutable `userdata/raw/v1/<version>/<id>/<uploadId>.json`.
  - `set_latest(version, gsv_player_id, latest: dict) -> None` — writes `userdata/latest/v1/<version>/<id>.json`.
  - `get_latest(version, gsv_player_id) -> dict | None`.
  - `save_unlinked(upload_id, payload) -> None`.
  - `list_latest(version) -> list[int]` — gsvPlayerIds with a latest overlay (used by overlay loader / future stats).
  - Local FS root = `${GD_DATA_DIR or repo}/data/userdata`; GCS prefix `userdata/` when `GCS_BUCKET` set (lazy import, mirrors `cloudstore.py`). Selection identical to `cloudstore._bucket()`.

- [ ] **Step 1: Write failing tests** (local-FS mode via `GD_DATA_DIR` tmp; no GCS)

```python
# tests/test_userstore.py
import json, os, importlib

def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    import server.userstore as us
    return importlib.reload(us)

def test_latest_roundtrip(tmp_path, monkeypatch):
    us = _store(tmp_path, monkeypatch)
    assert us.get_latest("galaxywave_delta", 42) is None
    us.set_latest("galaxywave_delta", 42, {"gsvPlayerId": 42, "charts": []})
    got = us.get_latest("galaxywave_delta", 42)
    assert got["gsvPlayerId"] == 42
    assert 42 in us.list_latest("galaxywave_delta")

def test_raw_is_immutable_separate_file(tmp_path, monkeypatch):
    us = _store(tmp_path, monkeypatch)
    us.save_upload("galaxywave_delta", 42, "u1", {"a": 1})
    us.save_upload("galaxywave_delta", 42, "u2", {"a": 2})
    base = os.path.join(str(tmp_path), "userdata", "raw", "v1", "galaxywave_delta", "42")
    assert set(os.listdir(base)) == {"u1.json", "u2.json"}
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_userstore.py -v` → FAIL.

- [ ] **Step 3: Implement `server/userstore.py`** — path helpers + JSON read/write; local FS with `os.makedirs(exist_ok=True)`; when `GCS_BUCKET` set, `blob(f"userdata/...").upload_from_string/download_as_text`; `list_latest` lists local dir or `bucket.list_blobs(prefix=...)`. Follow `cloudstore.py` env handling exactly.

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_userstore.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat: userdata store (local FS / GCS)"`

---

## Task 3: Ingestion + identity linking (`server/ingest.py`)

**Files:**
- Create: `server/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `server.userstore`; the engine's player table for linking (load `players.json` via a small `_load_players(version)` reused from a shared loader, or read directly).
- Produces:
  - `validate(payload: dict) -> list[str]` — returns list of error strings ([] = ok). Enforces: `schema==1`; `version` non-empty; `len(charts) <= 5000`; each chart `rank in RANKS`, `0<=achievement<=1` (or null when `exact is False`), `0<=level<=10`, `diff in {BAS,ADV,EXT,MAS}`; rejects keys `cookie`,`html`,`card`,`cardNumber` anywhere; total serialised size <= `MAX_BYTES` (8 MiB).
  - `link_identity(payload, version) -> tuple[int | None, float]` — match by normalised `playerName` + `drumSkillPoint` within tolerance against players.json; returns `(matched_db_index_or_None, confidence 0..1)`.
  - `ingest(payload: dict, version: str) -> tuple[int, dict]` — orchestrates validate → link → store; returns `(http_status, body)`. On link success: `set_latest` keyed by the payload's `gsvPlayerId` (the user-selected link target) with `visibility="private"`, `confidence`, normalised charts, `linkedDbId`; always `save_upload` raw. On ambiguous/failed link: `save_unlinked` + status 202 with `{"status":"quarantined", "reason":...}`. On validation error: 400 with `{"errors":[...]}`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest.py
import importlib
from server import ingest

def base_payload():
    return {"schema":1,"version":"galaxywave_delta","game":"gitadora","gtype":"dm",
            "gsvPlayerId":1,"scrapedAt":"2026-06-26T00:00:00Z","mode":"standard",
            "profile":{"playerName":"LASK","drumSkillPoint":7530.40},
            "charts":[{"sid":"2","name":"X","diff":"MAS","rank":"B","achievement":0.6515,
                       "exact":True,"level":8.45,"playCount":1,"clearCount":1}]}

def test_validate_ok():
    assert ingest.validate(base_payload()) == []

def test_validate_rejects_bad_rank_and_range():
    p = base_payload(); p["charts"][0]["rank"]="Z"; p["charts"][0]["achievement"]=2
    errs = ingest.validate(p); assert errs

def test_validate_rejects_sensitive_keys():
    p = base_payload(); p["cardNumber"]="HX..."
    assert any("card" in e.lower() for e in ingest.validate(p))

def test_validate_size_and_count_limits():
    p = base_payload(); p["charts"] = p["charts"]*6000
    assert any("5000" in e or "rows" in e.lower() for e in ingest.validate(p))
```
(Identity-link + store tests use a tmp `GD_DATA_DIR` and a minimal `players.json` fixture; assert quarantine when no name match, latest written when match.)

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_ingest.py -v` → FAIL.

- [ ] **Step 3: Implement `server/ingest.py`** — constants `RANKS`, `DIFFS`, `MAX_BYTES=8*1024*1024`, `MAX_ROWS=5000`; `validate` per interface; `_normalise_name` (casefold, strip); `link_identity` loads players.json (reuse engine's `PROC_DIR`), finds name matches, picks the one with `abs(sp - drumSkillPoint) < 75`, confidence from name-exact + sp-closeness; `ingest` orchestrates with `userstore` + a deterministic `upload_id` derived from a hash of `(gsvPlayerId, scrapedAt)` (no `Date.now`/random in server). Forbid sensitive keys via recursive scan.

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_ingest.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat: upload validation + identity linking + store orchestration"`

---

## Task 4: Overlay loader (`server/overlay.py`)

**Files:**
- Create: `server/overlay.py`
- Test: `tests/test_overlay.py`

**Interfaces:**
- Consumes: `server.userstore.get_latest`, `server.ranks`, the engine's `charts` catalog + chart index (pass them in to keep this pure/testable).
- Produces:
  - `build_overlay(latest: dict, chart_index: dict, charts: list, levels, ctx_fn) -> Overlay` where `chart_index` maps `(name,diff)->ci`, `ctx_fn(ci) -> (mu_ctx, var_ctx)` supplies the engine's context prior. `Overlay` is a dataclass with numpy arrays sized `C`: `played_mask` (bool), `eval_mask` (bool), `skill_mean`, `skill_sd`, `ach_mean`, `obs_weight`, and `obs_kind` (int code: 0 unknown / 1 gsv_exact / 2 upload_exact / 3 upload_rank). Charts not in `chart_index` (unknown to base catalog) are collected in `Overlay.extra` (list of dicts) — counted but not placed in the C-sized arrays.
  - For `exact` charts: `skill_mean=level*20*ach`, `skill_sd=0`, `obs_weight=1.0`, kind=2. For rank-only: use `ranks.obs_from_rank`. `eval_mask` = has exact or coarse obs.

- [ ] **Step 1: Write failing tests** (synthetic 3-chart catalog)

```python
# tests/test_overlay.py
import numpy as np
from server import overlay

def fixt():
    charts = [{"name":"X","diff":"MAS","level":8.45},
              {"name":"Y","diff":"EXT","level":6.0},
              {"name":"Z","diff":"ADV","level":4.0}]
    idx = {(c["name"],c["diff"]):i for i,c in enumerate(charts)}
    levels = np.array([8.45,6.0,4.0], dtype=np.float32)
    return charts, idx, levels

def test_exact_and_rank_obs():
    charts, idx, levels = fixt()
    latest = {"charts":[
        {"name":"X","diff":"MAS","rank":"B","achievement":0.6515,"exact":True,"level":8.45},
        {"name":"Y","diff":"EXT","rank":"S","exact":False,"level":6.0},
    ]}
    ov = overlay.build_overlay(latest, idx, charts, levels, ctx_fn=lambda ci:(0.9,0.01))
    assert ov.played_mask[0] and ov.played_mask[1] and not ov.played_mask[2]
    assert ov.obs_kind[0]==2 and ov.skill_sd[0]==0 and ov.obs_weight[0]==1.0
    assert ov.obs_kind[1]==3 and ov.skill_sd[1]>0 and 0.15<=ov.obs_weight[1]<=0.65
    assert abs(ov.skill_mean[0] - 8.45*20*0.6515) < 1e-3

def test_unknown_chart_goes_to_extra():
    charts, idx, levels = fixt()
    latest = {"charts":[{"name":"NEW","diff":"MAS","rank":"A","exact":False,"level":9.0}]}
    ov = overlay.build_overlay(latest, idx, charts, levels, ctx_fn=lambda ci:(0.85,0.01))
    assert len(ov.extra)==1 and ov.played_mask.sum()==0
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_overlay.py -v` → FAIL.

- [ ] **Step 3: Implement `server/overlay.py`** — `@dataclass Overlay`; `build_overlay` iterates `latest["charts"]`, resolves `ci`, fills arrays per interface, routes unknowns to `extra`.

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_overlay.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat: build per-chart observation overlay from upload"`

---

## Task 5: Engine overlay integration (`server/engine.py`)

**Files:**
- Modify: `server/engine.py`
- Test: `tests/test_engine_overlay.py`

**Interfaces:**
- Consumes: `server.overlay`, `server.ranks`, `server.userstore`.
- Produces (new/changed `Engine` methods; all default to existing behaviour when `overlay is None`):
  - `Engine.get_overlay(self, i) -> Overlay | None` — lazy: `userstore.get_latest(version, players[i]["playerId"])`; cache in `self._overlay_cache[i]`; respects visibility (load for the owner always; for others only if `visibility=="public"` — owner-vs-other is decided by the caller passing `as_owner`). Build via `overlay.build_overlay(..., ctx_fn=self._ctx_prior)`.
  - `Engine._ctx_prior(self, ci) -> (mu_ctx, var_ctx)` — wrap existing `_expected_ach`-style blend (same-bracket holders + kasegi + chart ach_mean) to return mean+variance.
  - `profile(self, i, overlay=None)` — when overlay present: use overlay obs (exact + coarse) for the sheet/signature/improve, filtering low-signal charts (level within `[my_med-2.5, my_max+0.6]`, `eval_mask`), and add an `enhanced: true`, `overlayStats` block (counts by obs_kind, allSongSkill check).
  - `similar_players(self, i, overlay=None)` — asymmetric taste: for each sparse peer j, score = IDF-weighted fraction of **j's** held charts that fall in i's overlay played/high-skill set (so a dense i is not punished by huge union). style = overlap-normalised cosine over shared charts weighted by `obs_weight`; latent downweighted for overlay users (multiply latent contribution by 0.3).
  - `song_recs(self, i, overlay=None)` — three classes: `discovery` (overlay `played_mask` false & neighbour taste), `practiceTargets` (played but below pool cutoff — show `neededAch` to clear cutoff), `skillUp` (exact/high-confidence coarse near/above cutoff). Reuse existing kasegi/expected-ach machinery.

- [ ] **Step 1: Write failing test** — build a tiny `Engine` against a synthetic processed dir (fixture builder writing minimal `charts.json/players.json/kasegi.json/meta.json/matrix.npz`), attach an overlay for player 0, assert `profile(0, overlay).enhanced is True`, that a dense overlay yields ≥1 `practiceTargets` entry, and that `similar_players` does not rank-drop player 0 to zero similarity purely due to large overlay union.

```python
# tests/test_engine_overlay.py  (skeleton — fixture builder fills minimal artifacts)
def test_profile_enhanced_and_practice_targets(tmp_engine_with_overlay):
    e, i, ov = tmp_engine_with_overlay
    prof = e.profile(i, overlay=ov)
    assert prof["enhanced"] is True
    recs = e.song_recs(i, overlay=ov)
    assert "practiceTargets" in recs and isinstance(recs["practiceTargets"], list)
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_engine_overlay.py -v` → FAIL.

- [ ] **Step 3: Implement** the methods above; keep all existing method signatures working with `overlay=None`. Do **not** modify matrix-building or base stats.

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_engine_overlay.py -v` and full `pytest -q` (existing behaviour unbroken) → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(engine): overlay-aware profile/similar/song_recs"`

---

## Task 6: App wiring — upload route + overlay injection (`server/app.py`)

**Files:**
- Modify: `server/app.py`
- Test: `tests/test_app_upload.py`

**Interfaces:**
- Consumes: `server.ingest`, engine overlay methods.
- Produces:
  - `--enable-upload` CLI flag + `GD_ENABLE_UPLOAD` env (default off). When on: `do_POST` handles `POST /api/upload` → read body (≤ MAX_BYTES, else 413) → `ingest.ingest(payload, VERSION)` → JSON response; `do_OPTIONS` for `/api/upload` returns CORS preflight (allow `https://p.eagate.573.jp` + same origin, `credentials: omit`). When off: `/api/upload` → 404 (cloud read-only service).
  - Player endpoints (`profile|similar|songs|all`) accept optional `?token=<shareToken>`; pass `overlay=e.get_overlay(pid, as_owner=token_matches)` so owners see private overlays and everyone sees public ones; responses include `enhanced` + `visibility`.
  - New `POST /api/publish` (upload-enabled only): `{gsvPlayerId, token, visibility}` flips `userdata/latest` visibility (private↔public) after token check.

- [ ] **Step 1: Write failing tests** — use `http.client` against a `ThreadingHTTPServer` on an ephemeral port with `GD_ENABLE_UPLOAD=1` and tmp `GD_DATA_DIR`: POST a valid payload → 200/202; GET `/api/upload` with server off → 404; OPTIONS returns allow-origin header.

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_app_upload.py -v` → FAIL.

- [ ] **Step 3: Implement** `do_POST`, `do_OPTIONS`, flag plumbing, overlay injection. Keep default deployment read-only (flag off).

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_app_upload.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(app): guarded upload route + overlay injection + publish toggle"`

---

## Task 7: Cloud Function entrypoint (`upload_fn/`)

**Files:**
- Create: `upload_fn/main.py`, `upload_fn/requirements.txt`
- Test: covered by `tests/test_ingest.py` (logic lives in `server/ingest.py`); add `tests/test_upload_fn.py` smoke that imports `main.upload` and calls it with a fake Flask-style request object.

**Interfaces:**
- Produces: `def upload(request)` (Functions Framework HTTP signature) → handles OPTIONS (CORS) + POST → `ingest.ingest`. Requires `GCS_BUCKET` env in cloud. `requirements.txt`: `google-cloud-storage`, `functions-framework`.

- [ ] **Step 1: Write failing smoke test** — fake request with `.get_json()` returning a valid payload, `.method=="POST"`; assert status 200/202.
- [ ] **Step 2: Verify fail.**
- [ ] **Step 3: Implement thin wrapper** importing `server.ingest` (add `sys.path` shim like `app.py`).
- [ ] **Step 4: Verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: upload Cloud Function entrypoint"`

---

## Task 8: Bookmarklet scraper (`web/bookmarklet.js`)

**Files:**
- Create: `web/bookmarklet.js`, `web/bookmarklet.test.mjs`, `tests/fixtures/eagate/music_cat0.html`, `tests/fixtures/eagate/music_detail_sid2.html`
- (Capture fixtures from the real authenticated pages, sanitised: strip query-strings/cookies; keep table/class structure.)

**Interfaces:**
- Produces (pure functions, exported when run under Node via `if (typeof module!=='undefined') module.exports={...}`):
  - `parseCategory(doc) -> [{sid, name, ranks:{BAS,ADV,EXT,MAS}}]` — read rows; rank from cell class `score_data_rank <R>` / `icon<R>`; `sid` from the song link's `sid` param.
  - `parseDetail(doc) -> {sid, charts:[{diff, level, rank, achievement, playCount, clearCount, hiScore, maxCombo}]}` — read the 4 `diff_*` tables; `達成率` row → fraction (`"65.15%"`→0.6515) or null.
  - `pickDetailQueue(catRows, spec) -> [sid]` — apply spec §4.3 priority; cap at `spec.detailCap` (default 120).
  - `buildPayload(profile, scraped, spec) -> payloadObject` — schema v1.
  - Runtime (browser only): `run(spec)` — rank sweep over `cat=0..36` with 1–1.5s throttle, detail fetches 2–3s + jitter with 429/5xx backoff, localStorage cache by `{version,sid}`, then `POST` to `spec.uploadUrl` (credentials omit) with download-JSON fallback on failure.

- [ ] **Step 1: Write failing Node tests** against fixtures using `jsdom` *or* a minimal `DOMParser` shim; assert `parseCategory` returns the right sids/ranks and `parseDetail` extracts `0.6515` for MAS.

```js
// web/bookmarklet.test.mjs (run: node --test web/bookmarklet.test.mjs)
import test from 'node:test'; import assert from 'node:assert';
import fs from 'node:fs'; import { JSDOM } from 'jsdom';
import bm from './bookmarklet.js';
test('parseDetail extracts exact achievement', () => {
  const dom = new JSDOM(fs.readFileSync('tests/fixtures/eagate/music_detail_sid2.html','utf8'));
  const r = bm.parseDetail(dom.window.document);
  const mas = r.charts.find(c=>c.diff==='MAS');
  assert.ok(Math.abs(mas.achievement - 0.6515) < 1e-4);
});
```

- [ ] **Step 2: Run, verify fail** — `node --test web/bookmarklet.test.mjs` → FAIL.
- [ ] **Step 3: Implement** the parser + runtime; keep parser pure & DOM-injection-safe (no eval; text extraction only).
- [ ] **Step 4: Run, verify pass** — `node --test web/bookmarklet.test.mjs` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat: e-amusement scraper bookmarklet + parser tests"`

---

## Task 9: Frontend — upload page & enhanced display (`web/`)

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`, `web/i18n.js`; `README.md`.

**Interfaces:**
- Consumes: `/api/upload`, `/api/publish`, player endpoints' `enhanced`/`visibility`.
- Produces: an "上傳完整資料" view that (1) shows the bookmarklet (drag-to-bookmarks link built from `web/bookmarklet.js`), (2) explains the throttle/time, (3) offers the download-JSON → file-upload fallback (same-origin POST), (4) shows link/quarantine result + share token, (5) a publish/unpublish toggle; plus an "enhanced" badge + `practiceTargets` section in profile/recs when `enhanced`.

- [ ] **Step 1:** Add i18n keys (4 locales) for all new strings.
- [ ] **Step 2:** Add the upload view + bookmarklet link + fallback uploader in `index.html`/`app.js`; wire `/api/upload` (file) and `/api/publish`.
- [ ] **Step 3:** Add `enhanced` badge + `practiceTargets` rendering in profile/recs; style in `styles.css`.
- [ ] **Step 4:** Manual smoke: `GD_ENABLE_UPLOAD=1 ./run.ps1`, load a downloaded sample payload via the file uploader, confirm overlay takes effect (profile shows `enhanced`, practiceTargets populated). Document in `README.md`.
- [ ] **Step 5: Commit** — `git commit -am "feat(web): upload page, fallback uploader, enhanced display"`

---

## Self-Review

**Spec coverage:** §2 page structure → Tasks 8 (parser) + fixtures. §4 acquisition/throttle/cache → Task 8 `run`. §5 ingestion/storage/CORS → Tasks 2,3,6,7. §6 overlay engine (rank prior, asymmetric similarity, profile/recs) → Tasks 1,4,5. §7 official stats layer → explicitly deferred (Phase 3, noted out-of-scope). §8 identity → Task 3 `link_identity` + quarantine. §9 visibility → Tasks 3 (default private), 5 (visibility-gated load), 6 (publish toggle), 9 (UI). §10 error handling → validation (3), backoff (8), silent overlay fallback (5). §11 testing → each task TDD. Covered.

**Placeholder scan:** No "TBD/TODO"; algorithmic code is concrete; standard plumbing (HTTP/file IO/DOM) specifies exact behaviour + tests. Fixtures must be captured from the live pages before Task 8 (called out in Task 8 Files).

**Type consistency:** `obs_kind` codes (0/1/2/3), `Overlay` array names, `RANK_BANDS`/`obs_from_rank` shapes, and the payload schema keys are used identically across Tasks 1,3,4,5,8. `get_overlay(i, as_owner=...)` consumed by Task 6 matches Task 5's definition.

**Phasing:** MVP = Tasks 1–9. Deferred: CORS direct POST (Task 6 sets headers but UI uses same-origin fallback), latent-SVD projection fix, `official_play_stats`, and **cross-player consumption of public overlays** (today similar/rivals inject only the *requesting* player's own overlay; a published dense uploader is not yet pulled into other players' peer comparisons — Phase 2; private never leaks, so this is a missing positive capability, not a safety issue).

**Post-implementation review fixes applied (adversarial review):** (1) overlay z-scores now propagate observation uncertainty per spec §6.1 — denominator `sqrt(bracket_std² + skill_sd²)` in both `_apply_overlay_profile.wz_ov` and `_signals_overlay`; (2) overlay cache now has a TTL + `Engine.invalidate_overlay(gsvPlayerId)` called from the upload/publish handlers so uploads & publish-flips take effect immediately; (3) `ingest._scan_forbidden` now also rejects card-number-shaped string *values* (16+ uppercase-alnum run), not just sensitive keys.

**Live run-through findings (eagate, 2026-06-26) — bookmarklet corrected & re-validated:**
- **`sid` is a constant game id (DrumMania = 2), NOT a per-song id.** The original parser keyed songs by `sid` and got the same id for every row. Reworked the whole bookmarklet to key songs by **name** (matching the server's `(name,diff)` chart identity) and capture each row's positional **`index`** + relative **`detailPath`**. `parseCategory` re-validated live: 16 rows, 16 distinct indices, correct names/ranks (`10,000,000,000` MAS=B). Touched `parseCategory`/`pickDetailQueue`/`buildPayload`/`run`/cache + fixtures + tests; `*Sids` spec fields → `*Names`.
- **`parseDetail` robustness fixes from real HTML:** level marker is a *nested* `<th class="diff_X"><div class="diff_X">N.NN</div></th>` (number only) — level regex now tolerates nesting; metric values carry units (e.g. `プレー回数 "2 回"`) — `parseIntOrNull` now extracts the leading integer. Achievement extraction confirmed live (`達成率 65.15%`). On a *played* song the live 最高ランク renders as a CSS clear-medal with no rank letter in the DOM, so `parseDetail` returns rank `'-'` and `buildPayload` falls back to the **list** rank.
- **OPEN ISSUE — music_detail addressing is state-dependent (exact-detail gated OFF by default).** Verified live: a direct deep-link to `music_detail.html?...&cat=N&index=M` intermittently returns **all-zero scores** (with `cat=0`) or **redirects to `/p/error/`**; the *same* URL returned real data only right after interacting with the matching list page. `index` is **per-category** (each `cat` restarts at 0), and `cat=`(empty) only addresses category 0. So the site tracks a server-side selection state that a stateless `fetch` doesn't reproduce. `run()` now gates `fetchDetails()` behind `spec.fetchDetails === true` (default off) so the MVP ships the **reliable 37-page rank sweep only** — the engine's Bayesian rank→achievement prior fills in achievement, and the bookmarklet aborts on maintenance/login pages (`isUnavailablePage`). Resolving exact-detail (reverse-engineer the selection-state / referer / XHR, or drive it via real per-song navigation) is a follow-up to be done WITHOUT brute-forcing the live server.
