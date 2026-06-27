"""
make_demo.py — synthesize a small, offline demo dataset for an instrument.

There is no gsv.fun access in some environments (and a real fetch takes minutes),
so this generates a plausible synthetic raw JSONL and runs it through the real
`build_dataset.build` pipeline, producing data/processed/<version>/<instrument>/.
Handy for previewing the UI (incl. the DrumMania ⇄ GuitarFreaks switcher) without
scraping anything. Deterministic (seeded) so reruns are stable.

Usage:
  python pipeline/make_demo.py --version demo --instrument guitar [--players 60]
  python pipeline/make_demo.py --version demo --instrument drum
  python server/app.py --version demo          # then open the browser
"""

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import build_dataset as bd  # noqa: E402

DIFFS = ["EXT", "MAS"]
# part pools per instrument: drum is single-part; guitar mixes guitar + bass.
PARTS = {"drum": ["D"], "guitar": ["G", "B"]}

SONG_WORDS_A = ["Neon", "Crimson", "Aurora", "Velvet", "Quantum", "Echo", "Saffron",
                "Lunar", "Cobalt", "Ember", "Static", "Mirage", "Prism", "Tidal",
                "Zephyr", "Onyx", "Halcyon", "Nova", "Cinder", "Glacier"]
SONG_WORDS_B = ["Drive", "Cascade", "Protocol", "Rhapsody", "Circuit", "Bloom",
                "Voyage", "Pulse", "Requiem", "Horizon", "Fracture", "Anthem",
                "Lullaby", "Odyssey", "Spark", "Reverie", "Gravity", "Storm"]


def _song_catalog(rng, n_songs):
    """A deterministic catalog of (name, diff, part, level) charts."""
    songs = []
    used = set()
    i = 0
    while len(songs) < n_songs:
        a = SONG_WORDS_A[i % len(SONG_WORDS_A)]
        b = SONG_WORDS_B[(i // len(SONG_WORDS_A)) % len(SONG_WORDS_B)]
        name = f"{a} {b}"
        i += 1
        if name in used:
            continue
        used.add(name)
        level = round(float(rng.uniform(4.0, 9.7)), 2)
        songs.append({"name": name, "level": level})
    return songs


def _gen(version, instrument, n_players, n_songs, seed=7):
    rng = np.random.default_rng(seed)
    parts = PARTS[instrument]
    songs = _song_catalog(rng, n_songs)
    # expand each song into one chart per (part, hardest diff) — for guitar this
    # yields distinct guitar (G) and bass (B) charts of the same song.
    charts = []
    for s in songs:
        diff = DIFFS[rng.integers(0, len(DIFFS))]
        for part in parts:
            charts.append({"name": s["name"], "diff": diff, "part": part,
                           "level": round(s["level"] + (0.2 if part == "B" else 0.0), 2)})
    C = len(charts)
    skill_field = bd.SKILLPOINT_FIELD[instrument]

    players = []
    for pi in range(n_players):
        ability = float(rng.uniform(0.78, 0.99))          # this player's base achievement
        sp_target = 1500 + int(ability * 8000)
        # pick ~50 charts near this player's level
        idx = rng.permutation(C)
        picked = []
        for ci in idx:
            lv = charts[ci]["level"]
            if lv <= ability * 10.5 + 0.6:
                picked.append(int(ci))
            if len(picked) >= 50:
                break
        rows = []
        for ci in picked:
            lv = charts[ci]["level"]
            ach = float(np.clip(ability + rng.normal(0, 0.04) - (lv - 6) * 0.012, 0.4, 1.0))
            rows.append({
                "name": charts[ci]["name"], "part": charts[ci]["part"],
                "diff": charts[ci]["diff"], "diff_value": lv,
                "achive_value": f"{ach * 100:.2f}%",
                "skill_value": round(lv * 20.0 * ach, 2),
            })
        rows.sort(key=lambda r: -r["skill_value"])
        hot, other = rows[:25], rows[25:50]
        players.append({
            "playerId": 10000 + pi,
            "playerName": f"DEMO_{instrument[:1].upper()}{pi:03d}",
            skill_field: float(sum(r["skill_value"] for r in hot + other)),
            "updateDate": "2026-06-27",
            "hot": {"point": sum(r["skill_value"] for r in hot), "data": hot},
            "other": {"point": sum(r["skill_value"] for r in other), "data": other},
        })

    os.makedirs(bd.RAW_DIR, exist_ok=True)
    raw_path = os.path.join(bd.RAW_DIR, f"players_{instrument}_{version}.jsonl")
    with open(raw_path, "w", encoding="utf-8") as fh:
        for p in players:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(os.path.join(bd.RAW_DIR, f"kasegi_{instrument}_{version}.json"),
              "w", encoding="utf-8") as fh:
        json.dump([], fh)
    return len(players), C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="demo")
    ap.add_argument("--instrument", default="guitar", choices=list(bd.SKILLPOINT_FIELD))
    ap.add_argument("--players", type=int, default=60)
    ap.add_argument("--songs", type=int, default=60)
    args = ap.parse_args()
    p, c = _gen(args.version, args.instrument, args.players, args.songs)
    print(f"[demo] generated {p} players x {c} charts for {args.instrument}/{args.version}")
    bd.build(version=args.version, instrument=args.instrument, min_support=2, svd_dim=8)


if __name__ == "__main__":
    main()
