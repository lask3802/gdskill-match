"""
ingest.py — upload payload validation, identity linking, and store orchestration.

Transport-agnostic: callers (the guarded `POST /api/upload` route in `app.py` and
the `upload_fn/` Cloud Function) hand in a parsed JSON payload and the engine
`version`; `ingest()` returns `(http_status, body_dict)` — no HTTP framework here.

Pipeline (spec §5, §8, §9):
  validate  -> reject bad schema / out-of-range fields / sensitive material
  link      -> self-attested probabilistic match (normalised name + skillpoint)
  store     -> always keep the immutable raw upload; on a confident link write the
               private overlay (`userdata/latest`), otherwise quarantine
               (`userdata/unlinked`) and never overwrite an existing overlay.

Hard rules honoured here:
  * Uploads are an overlay only — this module writes solely under `userdata/`
    (via `userstore`); it never touches matrix.npz or base per-chart stats.
  * Never store/transmit カードナンバー / cookies / raw HTML — rejected in validate.
  * Identity is self-attested; ambiguous/failed link -> quarantine, never overwrite.
  * Visibility is PRIVATE by default.
  * Deterministic ids only (hash of payload fields) — no Date.now / randomness.
"""

import hashlib
import json
import os
import re

from server import userstore

# --- shared payload contract (schema v1) ------------------------------------
SCHEMA_VERSION = 1
RANKS = {"SS", "S", "A", "B", "C", "D", "E", "-"}
DIFFS = {"BAS", "ADV", "EXT", "MAS"}
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 5000

# Keys that must never appear anywhere in an upload (case-insensitive substring).
FORBIDDEN_KEY_SUBSTRINGS = ("cookie", "html", "card")

# Card-number-shaped string VALUES are rejected too (defence in depth): the
# e-amusement カードナンバー is a 16-char uppercase alphanumeric run (e.g.
# "HX2AKJCDDZA6YZ1M"). The 10-char ギタドラ ID and short sids never match {16,}.
_CARDLIKE = re.compile(r"[A-Z0-9]{16,}")

# Skillpoint tolerance for accepting a name match as the same person.
SP_TOLERANCE = 75.0

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# --- validation -------------------------------------------------------------

def _scan_forbidden(obj, errors, path="payload"):
    """Recursively flag forbidden KEYS (cookie/html/card) and card-number-shaped
    string VALUES anywhere in the tree (a card number hidden under a benign key
    must not slip through into the immutable raw upload)."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            kl = str(key).lower()
            for bad in FORBIDDEN_KEY_SUBSTRINGS:
                if bad in kl:
                    errors.append(f"forbidden key '{key}' at {path} (no {bad}/raw HTML/card)")
                    break
            _scan_forbidden(val, errors, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            _scan_forbidden(val, errors, f"{path}[{i}]")
    elif isinstance(obj, str):
        if _CARDLIKE.search(obj):
            errors.append(f"forbidden card-number-like value at {path}")


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(payload):
    """Return a list of error strings ([] == ok)."""
    errors = []

    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]

    if payload.get("schema") != SCHEMA_VERSION:
        errors.append(f"schema must be {SCHEMA_VERSION}")

    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        errors.append("version must be a non-empty string")

    gsv_id = payload.get("gsvPlayerId")
    if not (isinstance(gsv_id, int) and not isinstance(gsv_id, bool) and gsv_id > 0):
        errors.append("gsvPlayerId must be a positive integer")

    # Reject sensitive material anywhere in the tree (cards / cookies / raw HTML).
    _scan_forbidden(payload, errors)

    charts = payload.get("charts")
    if not isinstance(charts, list):
        errors.append("charts must be a list")
        charts = []
    if len(charts) > MAX_ROWS:
        errors.append(f"too many charts: {len(charts)} > {MAX_ROWS} rows")

    for i, c in enumerate(charts):
        if not isinstance(c, dict):
            errors.append(f"charts[{i}] must be an object")
            continue
        rank = c.get("rank")
        if rank not in RANKS:
            errors.append(f"charts[{i}].rank '{rank}' not in {sorted(RANKS)}")
        diff = c.get("diff")
        if diff not in DIFFS:
            errors.append(f"charts[{i}].diff '{diff}' not in {sorted(DIFFS)}")
        level = c.get("level")
        if not _is_number(level) or not (0.0 <= level <= 10.0):
            errors.append(f"charts[{i}].level must be a number in [0, 10]")
        exact = c.get("exact")
        ach = c.get("achievement")
        if ach is None:
            if exact is True:
                errors.append(f"charts[{i}].achievement required when exact is true")
        elif not _is_number(ach) or not (0.0 <= ach <= 1.0):
            errors.append(f"charts[{i}].achievement must be a number in [0, 1]")

    # Size guard last (cheap-ish; covers the whole serialised body).
    try:
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if size > MAX_BYTES:
            errors.append(f"payload too large: {size} bytes > {MAX_BYTES}")
    except (TypeError, ValueError):
        errors.append("payload is not JSON-serialisable")

    return errors


# --- identity linking (self-attested, probabilistic) ------------------------

def _players_path(version, instrument="drum"):
    data_base = os.environ.get("GD_DATA_DIR") or os.path.join(ROOT, "data")
    return os.path.join(data_base, "processed", version, instrument, "players.json")


def _load_players(version, instrument="drum"):
    """Load the base players table for `version`; [] if absent/unreadable."""
    try:
        with open(_players_path(version, instrument), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _normalise_name(name):
    return (name or "").strip().casefold()


def link_identity(payload, version, instrument="drum"):
    """Probabilistic self-attested match (spec §8).

    Match by normalised playerName, then disambiguate by drumSkillPoint within
    `SP_TOLERANCE`. Returns `(matched_db_index | None, confidence 0..1)`. A failed
    or ambiguous match returns `(None, confidence)` so the caller quarantines.

    Uploads are DrumMania-only this round, so `instrument` defaults to drum.
    """
    profile = payload.get("profile") or {}
    target_name = _normalise_name(profile.get("playerName"))
    target_sp = profile.get("drumSkillPoint")

    players = _load_players(version, instrument)
    if not target_name or not players:
        return None, 0.0

    name_matches = [p for p in players if _normalise_name(p.get("name")) == target_name]
    if not name_matches:
        return None, 0.0

    # Name only (no skillpoint to disambiguate): accept a unique match weakly.
    if not _is_number(target_sp):
        if len(name_matches) == 1:
            return name_matches[0].get("id"), 0.5
        return None, 0.3  # several same-named players, cannot disambiguate

    best = min(name_matches, key=lambda p: abs(float(p.get("sp") or 0.0) - float(target_sp)))
    diff = abs(float(best.get("sp") or 0.0) - float(target_sp))
    closeness = 1.0 - min(diff / SP_TOLERANCE, 1.0)   # 1.0 == exact, 0.0 == >= tol
    if diff >= SP_TOLERANCE:
        return None, round(0.5 * closeness, 4)          # name hit but skill too far
    confidence = 0.5 + 0.5 * closeness                   # in [0.5, 1.0]
    return best.get("id"), round(confidence, 4)


# --- store orchestration ----------------------------------------------------

def _upload_id(gsv_player_id, scraped_at):
    """Deterministic id from (gsvPlayerId, scrapedAt) — no randomness/time."""
    h = hashlib.sha256(f"{gsv_player_id}:{scraped_at or ''}".encode("utf-8"))
    return h.hexdigest()[:16]


def _share_token(gsv_player_id, scraped_at, upload_id):
    """Deterministic, unguessable owner token gating private overlay reads."""
    h = hashlib.sha256(f"tok:{gsv_player_id}:{scraped_at or ''}:{upload_id}".encode("utf-8"))
    return h.hexdigest()[:32]


def _normalise_charts(charts):
    """Keep only the canonical overlay fields, coercing types defensively."""
    out = []
    for c in charts:
        if not isinstance(c, dict):
            continue
        rec = {
            "sid": str(c["sid"]) if c.get("sid") is not None else None,
            "name": c.get("name"),
            "diff": c.get("diff"),
            "rank": c.get("rank"),
            "achievement": float(c["achievement"]) if _is_number(c.get("achievement")) else None,
            "exact": bool(c.get("exact")),
            "level": float(c["level"]) if _is_number(c.get("level")) else None,
        }
        for opt in ("playCount", "clearCount", "hiScore", "maxCombo"):
            if c.get(opt) is not None:
                rec[opt] = c[opt]
        out.append(rec)
    return out


def ingest(payload, version, instrument="drum"):
    """Validate -> link -> store. Returns `(http_status, body_dict)`.

    * 400 -> `{"errors": [...]}` on validation failure (nothing stored).
    * 202 -> `{"status": "quarantined", ...}` on ambiguous/failed identity link;
             raw kept + quarantined, an existing overlay is never overwritten.
    * 200 -> `{"status": "linked", ...}` on a confident link; private overlay
             written under the self-attested gsvPlayerId.
    """
    errors = validate(payload)
    if errors:
        return 400, {"errors": errors}

    gsv_id = int(payload["gsvPlayerId"])
    scraped_at = payload.get("scrapedAt")
    upload_id = _upload_id(gsv_id, scraped_at)

    # Immutable raw upload is the source of truth — always kept (spec §5.4).
    userstore.save_upload(version, gsv_id, upload_id, payload)

    matched_idx, confidence = link_identity(payload, version, instrument)
    if matched_idx is None:
        userstore.save_unlinked(upload_id, payload)
        return 202, {
            "status": "quarantined",
            "reason": "identity could not be linked (self-attested name/skill did not match)",
            "confidence": confidence,
            "uploadId": upload_id,
        }

    charts = _normalise_charts(payload.get("charts") or [])
    token = _share_token(gsv_id, scraped_at, upload_id)
    latest = {
        "schema": SCHEMA_VERSION,
        "version": version,
        "gsvPlayerId": gsv_id,
        "visibility": "private",          # spec §9 — private by default
        "confidence": confidence,
        "linkedDbId": matched_idx,
        "token": token,
        "scrapedAt": scraped_at,
        "mode": payload.get("mode"),
        "profile": payload.get("profile"),
        "charts": charts,
        "uploadId": upload_id,
    }
    userstore.set_latest(version, gsv_id, latest)
    return 200, {
        "status": "linked",
        "gsvPlayerId": gsv_id,
        "confidence": confidence,
        "linkedDbId": matched_idx,
        "token": token,
        "charts": len(charts),
        "visibility": "private",
        "uploadId": upload_id,
    }
