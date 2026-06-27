# GuitarFreaks as a first-class instrument — design

Date: 2026-06-27
Status: approved (brainstorming) → implementation

## Goal

Replicate the existing **DrumMania** experience for **GuitarFreaks**, following the
same method ("根據 drummania 的方法"): same statistical engine, same page, same
recommendations — just a second instrument. Plus:

1. A **GitHub issue** entry point (icon → repo issues) in the header.
2. A **DM ⇄ GF** instrument switcher in the header.
3. Harden the **public** repo's privacy/security.

Approved decisions:

- Full-stack parameterization by `instrument ∈ {drum, guitar}`; **drum stays the
  default** so existing behavior is byte-for-byte unchanged.
- Data layout nests per instrument: `data/processed/<version>/<instrument>/…`
  (symmetric — both instruments are first-class).
- Top-right header hosts the GitHub icon + the DM⇄GF switcher; a single shared SPA.
- Repo stays **public**; comprehensive hardening instead of going private.
- Full-data **upload/overlay stays DrumMania-only** this round (the e-amusement
  bookmarklet scrapes `gtype=dm`; extending it to `gtype=gf/bs` is a separate effort).

## Non-goals

- Extending the upload bookmarklet / ingest / overlay to GuitarFreaks.
- Any change to the recommendation math itself.
- Fetching real GuitarFreaks data in this change (it's a runtime step).

## Architecture / the instrument axis

`instrument` is threaded end to end; `"drum"` is the default everywhere.

### Data layout

```
data/
├─ raw/
│  ├─ players_drum_<version>.jsonl     kasegi_drum_<version>.json     (existing)
│  └─ players_guitar_<version>.jsonl   kasegi_guitar_<version>.json   (new)
└─ processed/<version>/<instrument>/
   └─ charts.json players.json kasegi.json meta.json matrix.npz
```

Both drum and guitar nest under `<instrument>/`. Migration is automatic: `data/`
is gitignored and rebuilt on first run; the cloud updater rebuilds + re-uploads.

### Chart identity = (name, diff, part)

- Drum: `part` is always `"D"` → identical effective uniqueness; `charts.json`
  simply gains a `"part"` field. No behavioral change.
- Guitar: a song's **guitar (G)** and **bass (B)** charts are distinct entries.
  Required — GuitarFreaks skill spans both guitar and bass charts, so collapsing
  them on `(name, diff)` would corrupt the matrix.

### Module changes

| Module | Change |
|--------|--------|
| `pipeline/fetch_data.py` | `--instrument`; per-instrument GraphQL field map: drum→`drumSkill`/`drumSkillPoint`/`type:d`, guitar→`guitarSkill`/`guitarSkillPoint`/`type:g`. Raw filenames + kasegi `type` parameterized. **The exact gsv guitar skill field name is isolated in one map** and flagged for a live confirm. |
| `pipeline/build_dataset.py` | `--instrument`; chart key `(name,diff,part)`; reads the instrument-specific raw file + skillpoint field; writes to `processed/<version>/<instrument>/`. |
| `updater/{fetch_data,build_dataset}.py` | Kept byte-identical to `pipeline/` copies (a CI/diff check guards this). |
| `server/engine.py` | `Engine(version, instrument="drum")`; cache keyed by `(version,instrument)`; `processed/<version>/<instrument>/`; gsv URL `/d`↔`/g`; chart_index keyed `(name,diff,part)`. Overlay path runs only for drum. |
| `server/app.py` | `?inst=` on API calls (validated against available instruments, default drum); `/api/meta` returns `instrument` + `instruments` list. Engine cache per (version,instrument). |
| `server/cloudstore.py` | Per-instrument artifact sync; `ensure_artifacts(version)` covers all configured instruments. |
| `server/ingest.py` | `_players_path` instrument-aware (default drum); upload remains drum-only. |
| `updater/main.py` | Rebuild + upload all configured instruments. |
| `run.ps1` / `run.sh` | `-Instrument` / `INSTRUMENT`; default builds drum, optional guitar. |

### API

- `GET /api/meta` → `{ instrument, instruments: ["drum","guitar"], version, … }`.
- All player/search/top endpoints accept `?inst=guitar` (default drum). Unknown
  instrument → 400.

## Front-end (shared SPA, top-right header)

- Global `INST` state, default `"drum"`, persisted in `localStorage` + `?inst=`
  URL param; deep-links carry it alongside `?p=`.
- Header right side: **GitHub icon** (anchor to
  `https://github.com/lask3802/gdskill-match/issues`, `target=_blank rel=noopener`)
  and a **DM ⇄ GF** pill switcher.
- Switching instrument: update state → re-theme static chrome (doc title, sub
  label, YouTube search term) → reload the current/default player for that
  instrument.
- Upload section is hidden when `INST==="guitar"` (DM-only), with a one-line note.
- i18n: add instrument-name keys (`instDrum`, `instGuitar`) and parameterize the
  few DrumMania-literal strings (`docTitle`, `metaSub` console label, YouTube
  term) across zh/en/ja/ko. The pad-grid metaphor is kept (cosmetic).

## Privacy / security hardening (repo stays public)

- **Audit**: confirm no secrets / GCP project IDs / third-party player data / PII
  are committed. (Done in design phase: `deploy.sh` is env-only; eagate fixtures
  are explicitly sanitised/synthetic; `data/` is gitignored.) Spot-check git
  history for stray data blobs.
- **Harden ignores**: ensure `.gitignore` / `.gcloudignore` / `.dockerignore`
  exclude `data/`, `userdata/`, and any token/secret files; align the three.
- **`SECURITY.md`**: responsible-disclosure contact + scope.
- **README**: short privacy/security section (no PII collected; scraped data not
  redistributed; upload route off by default in cloud).

## Testing / verification (no live gsv access in this env)

- `tests/test_build_guitar.py`: feed a synthetic guitar raw JSONL where one song
  has both G and B parts at the same diff → assert they stay distinct in
  `charts.json` and the matrix; `part` present.
- `tests/test_engine_guitar.py`: build a synthetic guitar `processed/<v>/guitar/`
  → `Engine(v,"guitar")` loads; gsv URL ends `/g`; profile/similar/songs return
  the expected shapes.
- `tests/test_app_instrument.py`: `?inst=guitar` routes to the guitar engine;
  `/api/meta` lists instruments; unknown instrument → 400.
- Update existing drum tests' synthetic-dataset helpers to the nested layout;
  **all existing tests stay green**.
- `pipeline/make_demo.py` (new): synthesize a tiny demo dataset for an instrument
  so the GF page is viewable offline. Used to launch the server locally and
  visually verify the switcher, GF page, and GitHub icon.

## Risks

- **gsv guitar GraphQL field name** (`guitarSkill` assumed) — isolated in one map;
  needs a single live run to confirm. Does not block the testable core.
- **Cloud artifact path change** — self-heals: `ensure_artifacts` bootstraps
  (scrape+build) when the new nested path is missing after deploy.
