# Security & Privacy Policy

GD Skill Match is an open-source, read-only analysis tool for public GITADORA
skill data. This document covers how to report problems and what the project does
to protect data.

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue for an
exploitable vulnerability.

- Preferred: open a [GitHub Security Advisory](https://github.com/lask3802/gdskill-match/security/advisories/new)
  (private to maintainers).
- Alternatively, open a regular [issue](https://github.com/lask3802/gdskill-match/issues)
  that only says you have a security report and asks for a private contact — do
  **not** include exploit details in the public issue.

We aim to acknowledge reports within a few days. Please allow reasonable time for
a fix before any public disclosure.

## What this project does to protect data

- **No secrets in the repo.** The repository is public and contains no API keys,
  passwords, tokens, GCP project IDs, or service-account identifiers. `deploy.sh`
  reads every account identifier from the environment at deploy time
  (`PROJECT_ID`, `BILLING_ACCOUNT`, …). `.gitignore` / `.gcloudignore` /
  `.dockerignore` block common secret files (`.env`, `*.key`, `*.pem`,
  `*-key.json`, `credentials*.json`, …).
- **No third-party personal data is redistributed.** Scraped player data and all
  derived artifacts live under `data/` (and uploads under `data/userdata/`),
  which are git-ignored and fetched/built on first run — they are never committed.
- **The public service is read-only.** The Cloud Run deployment exposes only
  `GET` endpoints. The full-data upload routes (`POST /api/upload`, `/api/publish`)
  are **off by default** and only mount with `--enable-upload` / `GD_ENABLE_UPLOAD=1`.
- **Uploads never carry sensitive material.** The ingest layer rejects any payload
  containing cookies, raw HTML, or card-number-shaped values, and stores only the
  normalized skill overlay. It writes solely under `userdata/`; it never mutates
  the base dataset. See `server/ingest.py`.
- **Uploaded overlays are private by default.** An overlay is visible only to its
  owner (via an unguessable share token) until they explicitly publish it.
- **No card numbers / cookies / e-amusement credentials** are ever accessed,
  stored, or transmitted by the upload bookmarklet or the server.

## Data sources

Player data comes from the community site [gsv.fun](http://gsv.fun) (public
GraphQL API). The tool reads only public data and links back to each player's gsv
profile. See the README for the methodology and its limitations.
