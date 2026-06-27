"""
build_dataset.py — Turn raw gsv.fun JSONL into the analysis artifacts the app loads.

Outputs (under data/processed/<version>/<instrument>/):
  charts.json      catalog of every distinct chart (name, diff, part, level, pool, stats)
  players.json     per-player metadata (id, name, skill point, hot/other cutoffs, sheet)
  matrix.npz       dense float32 player x chart skill matrix + presence + chart arrays + SVD
  meta.json        version, instrument, counts, build timestamp, kasegi summary

Chart identity = (name, diff, part). For DrumMania `part` is always "D" (a single
part), so the catalog is identical to the historical (name, diff) keying — it just
gains a `part` field. For GuitarFreaks a song's guitar (G) and bass (B) charts at
the same difficulty are DISTINCT entries: GF skill spans both parts.

HOT and OTHER pools are a clean partition by song vintage, so each chart gets one
pool. Skill formula: skill_value = level * 20 * achievement.

Run:  python pipeline/build_dataset.py [--version galaxywave_delta]
                                       [--instrument drum|guitar]
                                       [--min-support 5] [--svd-dim 32]
"""

import argparse
import json
import os
import statistics
from datetime import datetime, timezone
import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_BASE = os.environ.get("GD_DATA_DIR") or os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_BASE, "raw")
PROC_DIR = os.path.join(DATA_BASE, "processed")

DIFF_ORDER = {"BAS": 0, "ADV": 1, "EXT": 2, "MAS": 3}

# Per-instrument gsv skillpoint field carried in the raw records, and the canonical
# single-part code for instruments that have only one part (DrumMania).
SKILLPOINT_FIELD = {"drum": "drumSkillPoint", "guitar": "guitarSkillPoint"}
SINGLE_PART = {"drum": "D"}          # guitar has real per-chart parts (G / B)
DEFAULT_PART = {"drum": "D", "guitar": "G"}


def parse_ach(s):
    """'97.54%' -> 0.9754 ; robust to missing."""
    if s is None:
        return None
    s = str(s).strip().rstrip("%")
    try:
        return float(s) / 100.0
    except ValueError:
        return None


def chart_key(name, diff, part):
    return f"{name}␟{diff}␟{part}"  # unit separator to avoid collisions


def chart_part(rec, instrument):
    """Canonical part code for a raw chart record. DrumMania collapses to a single
    part ("D") so its catalog is unchanged; GuitarFreaks keeps the real G/B part."""
    single = SINGLE_PART.get(instrument)
    if single is not None:
        return single
    return rec.get("part") or DEFAULT_PART.get(instrument, "G")


def load_players(version, instrument="drum"):
    path = os.path.join(RAW_DIR, f"players_{instrument}_{version}.jsonl")
    players = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                players.append(json.loads(line))
    return players


def build(version="galaxywave_delta", instrument="drum", min_support=5, svd_dim=32):
    """Build the analysis artifacts for one (version, instrument) into
    processed/<version>/<instrument>/. Returns the output directory path."""
    skill_field = SKILLPOINT_FIELD.get(instrument, "drumSkillPoint")

    players = load_players(version, instrument)
    # keep only players with a full-ish sheet and positive skill
    players = [p for p in players if (p.get(skill_field) or 0) > 0
               and (p.get("hot", {}).get("data") or p.get("other", {}).get("data"))]
    P = len(players)
    print(f"[build] instrument={instrument} players: {P}")

    # ---- pass 1: gather charts + per-chart raw stats ----
    chart_meta = {}  # key -> dict
    for p in players:
        for pool in ("hot", "other"):
            for r in (p.get(pool, {}).get("data") or []):
                part = chart_part(r, instrument)
                k = chart_key(r["name"], r["diff"], part)
                m = chart_meta.get(k)
                if m is None:
                    m = {
                        "name": r["name"], "diff": r["diff"], "part": part,
                        "levels": [], "skills": [], "achs": [],
                        "pool_hot": 0, "pool_other": 0,
                    }
                    chart_meta[k] = m
                if r.get("diff_value") is not None:
                    m["levels"].append(float(r["diff_value"]))
                if r.get("skill_value") is not None:
                    m["skills"].append(float(r["skill_value"]))
                a = parse_ach(r.get("achive_value"))
                if a is not None:
                    m["achs"].append(a)
                m["pool_" + pool] += 1

    chart_keys = list(chart_meta.keys())
    chart_keys.sort(key=lambda k: (-(chart_meta[k]["pool_hot"] + chart_meta[k]["pool_other"]),
                                    chart_meta[k]["name"], chart_meta[k]["part"]))
    chart_index = {k: i for i, k in enumerate(chart_keys)}
    C = len(chart_keys)
    print(f"[build] distinct charts: {C}")

    charts = []
    levels = np.zeros(C, dtype=np.float32)
    pool_is_hot = np.zeros(C, dtype=np.uint8)
    for i, k in enumerate(chart_keys):
        m = chart_meta[k]
        count = m["pool_hot"] + m["pool_other"]
        is_hot = m["pool_hot"] >= m["pool_other"]
        level = statistics.median(m["levels"]) if m["levels"] else 0.0
        sk = m["skills"]
        ac = m["achs"]
        charts.append({
            "id": i,
            "name": m["name"],
            "diff": m["diff"],
            "part": m["part"],
            "level": round(level, 2),
            "pool": "hot" if is_hot else "other",
            "count": count,
            "skill_mean": round(statistics.mean(sk), 2) if sk else 0.0,
            "skill_std": round(statistics.pstdev(sk), 2) if len(sk) > 1 else 0.0,
            "skill_max": round(max(sk), 2) if sk else 0.0,
            "ach_mean": round(statistics.mean(ac), 4) if ac else 0.0,
            "ach_std": round(statistics.pstdev(ac), 4) if len(ac) > 1 else 0.0,
        })
        levels[i] = level
        pool_is_hot[i] = 1 if is_hot else 0

    # ---- pass 2: build matrices ----
    skill_mat = np.zeros((P, C), dtype=np.float32)
    ach_mat = np.zeros((P, C), dtype=np.float32)
    presence = np.zeros((P, C), dtype=np.uint8)

    player_meta = []
    for pi, p in enumerate(players):
        hot_vals, other_vals = [], []
        for pool in ("hot", "other"):
            for r in (p.get(pool, {}).get("data") or []):
                part = chart_part(r, instrument)
                ci = chart_index[chart_key(r["name"], r["diff"], part)]
                sv = float(r.get("skill_value") or 0.0)
                skill_mat[pi, ci] = sv
                presence[pi, ci] = 1
                a = parse_ach(r.get("achive_value"))
                if a is not None:
                    ach_mat[pi, ci] = a
                (hot_vals if pool == "hot" else other_vals).append(sv)
        hot_cut = min(hot_vals) if hot_vals else 0.0
        other_cut = min(other_vals) if other_vals else 0.0
        player_meta.append({
            "id": pi,
            "playerId": p.get("playerId"),
            "name": p.get("playerName"),
            "sp": round(float(p.get(skill_field) or 0.0), 2),
            "hotPoint": round(float(p.get("hot", {}).get("point") or 0.0), 2),
            "otherPoint": round(float(p.get("other", {}).get("point") or 0.0), 2),
            "hotCount": len(hot_vals),
            "otherCount": len(other_vals),
            "hotCutoff": round(hot_cut, 2),
            "otherCutoff": round(other_cut, 2),
            "updateDate": p.get("updateDate"),
        })

    # ---- per-chart z-score params (over holders only) ----
    counts = presence.sum(axis=0).astype(np.float32)         # holders per chart
    sums = skill_mat.sum(axis=0)
    means = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    sq = (skill_mat ** 2).sum(axis=0)
    var = np.divide(sq, counts, out=np.zeros_like(sq), where=counts > 0) - means ** 2
    var = np.clip(var, 1e-6, None)
    stds = np.sqrt(var)

    support_mask = counts >= min_support      # charts usable for similarity space
    print(f"[build] charts with support>={min_support}: {int(support_mask.sum())}")

    # ---- z-scored matrix over supported charts (0 where absent) ----
    z = np.zeros_like(skill_mat)
    idx = np.where(presence > 0)
    z[idx] = (skill_mat[idx] - means[idx[1]]) / stds[idx[1]]
    z[:, ~support_mask] = 0.0
    z_present = z * (presence > 0)   # keep zeros for absent

    # ---- SVD latent style embeddings (the "trained model") ----
    # L2-normalize each player's z vector first so SVD captures style direction,
    # not magnitude (overall level).
    zn = normalize(z_present, norm="l2", axis=1)
    dim = min(svd_dim, max(2, min(P, int(support_mask.sum())) - 1))
    svd = TruncatedSVD(n_components=dim, random_state=42)
    emb = svd.fit_transform(zn).astype(np.float32)           # P x dim
    emb = normalize(emb, norm="l2", axis=1)
    print(f"[build] SVD dim={dim} explained var ratio sum="
          f"{float(svd.explained_variance_ratio_.sum()):.3f}")

    # ---- kasegi summary (bracket -> {hot:[{chart,...}], other:[...]}) ----
    kasegi_path = os.path.join(RAW_DIR, f"kasegi_{instrument}_{version}.json")
    kasegi = []
    if os.path.exists(kasegi_path):
        with open(kasegi_path, "r", encoding="utf-8") as f:
            kasegi = json.load(f)

    # ---- write artifacts ----
    out = os.path.join(PROC_DIR, version, instrument)
    os.makedirs(out, exist_ok=True)

    with open(os.path.join(out, "charts.json"), "w", encoding="utf-8") as f:
        json.dump(charts, f, ensure_ascii=False)
    with open(os.path.join(out, "players.json"), "w", encoding="utf-8") as f:
        json.dump(player_meta, f, ensure_ascii=False)
    with open(os.path.join(out, "kasegi.json"), "w", encoding="utf-8") as f:
        json.dump(kasegi, f, ensure_ascii=False)

    np.savez_compressed(
        os.path.join(out, "matrix.npz"),
        skill=skill_mat,
        presence=presence,
        ach=ach_mat,
        levels=levels,
        pool_is_hot=pool_is_hot,
        chart_count=counts,
        chart_mean=means,
        chart_std=stds,
        support_mask=support_mask,
        emb=emb,
    )

    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "version": version,
            "instrument": instrument,
            "players": P,
            "charts": C,
            "supportCharts": int(support_mask.sum()),
            "minSupport": min_support,
            "svdDim": int(dim),
            "kasegiBrackets": [k["scope"] for k in kasegi],
            "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, f, ensure_ascii=False, indent=2)

    print(f"[build] wrote artifacts to {out}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="galaxywave_delta")
    ap.add_argument("--instrument", default="drum", choices=["drum", "guitar"])
    ap.add_argument("--min-support", type=int, default=5,
                    help="charts held by fewer than this many players are kept in the "
                         "catalog but flagged low-support (excluded from similarity space)")
    ap.add_argument("--svd-dim", type=int, default=32)
    args = ap.parse_args()
    build(version=args.version, instrument=args.instrument,
          min_support=args.min_support, svd_dim=args.svd_dim)


if __name__ == "__main__":
    main()
