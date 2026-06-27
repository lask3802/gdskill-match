"""
fetch_data.py — Download GITADORA skill data from Gitadora Skill Viewer (gsv.fun).

Data source: gsv.fun GraphQL API (community site; players upload their official
eAmusement skill sheets via a bookmarklet). We pull, for the target version and
instrument (DrumMania or GuitarFreaks):
  1. The full player list (playerId, name, drumSkillPoint, guitarSkillPoint, ...)
  2. Each player's full skill table (HOT 25 + OTHER 25 songs, each with
     skill_value / achievement% / chart level / part)
  3. The "kasegi" aggregate tables (per 500-point skill bracket: which songs the
     players in that bracket actually use for skill, and the average skill earned)

DrumMania (`type:d`, field `drumSkill`) is the default; GuitarFreaks (`type:g`,
field `guitarSkill`) is selected with `--instrument guitar`. Raw files are scoped
by instrument: players_<instrument>_<version>.jsonl / kasegi_<instrument>_<version>.json.

Uses only the Python standard library (urllib) so it runs with zero pip installs.
Resumable: per-player results are appended to a JSONL cache; rerunning skips
players already fetched.

Usage:
  python pipeline/fetch_data.py [--version galaxywave_delta]
                                [--instrument drum|guitar] [--workers 12]
"""

import argparse
import json
import os
import sys
import time
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

GRAPHQL_URL = "http://gsv.fun/graphql"
USER_AGENT = "gdskill-match/1.0 (local analysis tool; respectful single pull)"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_BASE = os.environ.get("GD_DATA_DIR") or os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_BASE, "raw")

# Per-instrument gsv schema knobs. `type` selects the game; `skillField` is the
# per-user skill table field; `skillPoint` is the scalar skillpoint key. The
# guitar field names mirror gsv's drum schema by symmetry (the `users` query
# already exposes both drumSkillPoint and guitarSkillPoint) — confirm `guitarSkill`
# against a live response if a guitar fetch ever returns empty sheets.
INSTRUMENTS = {
    "drum":   {"type": "d", "skillField": "drumSkill",   "skillPoint": "drumSkillPoint"},
    "guitar": {"type": "g", "skillField": "guitarSkill", "skillPoint": "guitarSkillPoint"},
}

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def players_filename(version, instrument):
    return os.path.join(RAW_DIR, f"players_{instrument}_{version}.jsonl")


def kasegi_filename(version, instrument):
    return os.path.join(RAW_DIR, f"kasegi_{instrument}_{version}.json")


def user_skill_query(player_id, version, instrument):
    """Build the per-user skill GraphQL query for an instrument."""
    cfg = INSTRUMENTS[instrument]
    return (
        f"{{ user(playerId:{player_id}, type:{cfg['type']}, version: {version}) {{"
        f" playerName {cfg['skillPoint']} updateDate"
        f" {cfg['skillField']} {{"
        f"   hot   {{ point data {{ name part diff skill_value achive_value diff_value }} }}"
        f"   other {{ point data {{ name part diff skill_value achive_value diff_value }} }}"
        f" }} }} }}"
    )


def make_record(full, u, instrument):
    """Normalise a fetched `user` payload into the raw JSONL record. Carries the
    instrument's own skillpoint field (drumSkillPoint / guitarSkillPoint)."""
    cfg = INSTRUMENTS[instrument]
    skill_point = cfg["skillPoint"]
    skill = (full.get(cfg["skillField"]) or {})
    return {
        "playerId": u["playerId"],
        "playerName": full.get("playerName") or u.get("playerName"),
        skill_point: full.get(skill_point),
        "updateDate": full.get("updateDate") or u.get("updateDate"),
        "hot": skill.get("hot") or {"point": 0, "data": []},
        "other": skill.get("other") or {"point": 0, "data": []},
    }


def gql(query, retries=4, timeout=40):
    """POST a GraphQL query, return parsed `data` dict. Retries on transient errors."""
    body = json.dumps({"query": query}).encode("utf-8")
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                GRAPHQL_URL,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if "errors" in payload and payload["errors"]:
                raise RuntimeError(f"GraphQL errors: {payload['errors']}")
            return payload.get("data")
        except Exception as e:  # noqa: BLE001 - want broad retry
            last_err = e
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"GraphQL request failed after {retries} tries: {last_err}")


def fetch_user_list(version):
    log(f"[users] fetching player list for version={version} ...")
    data = gql(
        f"""{{ users(version: {version}) {{
            playerId playerName drumSkillPoint guitarSkillPoint updateDate updateCount
        }} }}"""
    )
    users = data["users"]
    log(f"[users] {len(users)} total players")
    return users


def fetch_user_skill(player_id, version, instrument):
    data = gql(user_skill_query(player_id, version, instrument))
    return data["user"]


def fetch_kasegi(version, instrument, scopes):
    cfg = INSTRUMENTS[instrument]
    out = []
    for s in scopes:
        try:
            data = gql(
                f"""{{ kasegiNew(scope:{s}, type:{cfg['type']}, version: {version}) {{
                    scope count
                    hot   {{ name diff part diffValue averageSkill count averagePlayerSKill }}
                    other {{ name diff part diffValue averageSkill count averagePlayerSKill }}
                }} }}"""
            )
            k = data["kasegiNew"]
            if k and k.get("count"):
                out.append(k)
                log(f"[kasegi] scope={s} players={k['count']} "
                    f"hot={len(k.get('hot') or [])} other={len(k.get('other') or [])}")
        except Exception as e:  # noqa: BLE001
            log(f"[kasegi] scope={s} failed: {e}")
    return out


def load_done_ids(jsonl_path):
    done = set()
    if os.path.exists(jsonl_path):
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    done.add(rec["playerId"])
                except Exception:  # noqa: BLE001
                    pass
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="galaxywave_delta")
    ap.add_argument("--instrument", default="drum", choices=list(INSTRUMENTS))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--min-skill", type=float, default=0.01,
                    help="skip players whose skillPoint is below this")
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    version = args.version
    instrument = args.instrument
    skill_point = INSTRUMENTS[instrument]["skillPoint"]

    users = fetch_user_list(version)
    with open(os.path.join(RAW_DIR, f"users_{version}.json"), "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False)

    targets = [u for u in users if (u.get(skill_point) or 0) >= args.min_skill]
    log(f"[plan] {len(targets)} players have {instrument} skill >= {args.min_skill}")

    jsonl_path = players_filename(version, instrument)
    done = load_done_ids(jsonl_path)
    if done:
        log(f"[resume] {len(done)} players already cached, skipping those")
    todo = [u for u in targets if u["playerId"] not in done]
    log(f"[plan] fetching {len(todo)} players with {args.workers} workers")

    write_lock = threading.Lock()
    counter = {"ok": 0, "fail": 0}
    t0 = time.time()

    def work(u):
        pid = u["playerId"]
        try:
            full = fetch_user_skill(pid, version, instrument)
            rec = make_record(full, u, instrument)
            with write_lock:
                with open(jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                counter["ok"] += 1
                n = counter["ok"] + counter["fail"]
                if n % 50 == 0 or n == len(todo):
                    rate = n / max(time.time() - t0, 0.001)
                    log(f"[progress] {n}/{len(todo)} ok={counter['ok']} "
                        f"fail={counter['fail']} ({rate:.1f}/s)")
            return True
        except Exception as e:  # noqa: BLE001
            with write_lock:
                counter["fail"] += 1
            log(f"[warn] player {pid} failed: {e}")
            return False

    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(work, u) for u in todo]
            for _ in as_completed(futs):
                pass

    log(f"[done] players ok={counter['ok']} fail={counter['fail']} "
        f"elapsed={time.time()-t0:.1f}s")

    # kasegi aggregate tables (skill brackets in 500-pt steps)
    scopes = list(range(1500, 9500, 500))
    kasegi = fetch_kasegi(version, instrument, scopes)
    with open(kasegi_filename(version, instrument), "w", encoding="utf-8") as f:
        json.dump(kasegi, f, ensure_ascii=False)
    log(f"[done] kasegi brackets={len(kasegi)} written")

    log("[complete] raw data fetch finished.")


if __name__ == "__main__":
    main()
