"""
engine.py — GITADORA DrumMania similarity & recommendation engine.

Loads the artifacts produced by pipeline/build_dataset.py and answers, for any
player A:
  * profile()         — what A is good at (signature strengths, level reach, balance)
  * similar_players() — players whose taste / style resemble A's (level-agnostic)
  * rivals()          — reachable rivals to add (near-peers + growth rivals)
  * song_recs()       — charts to play: neighbour-discovery picks + skill-up picks

Methodology (refined after a methodology review)
------------------------------------------------
A player is a sparse vector over ~3920 charts; only their best 50 charts are known
(heavy survivorship bias). We therefore keep THREE explicit, separately-reported
similarity signals instead of one opaque score:

  taste  : IDF-weighted Jaccard over which charts two players hold (globally popular
           charts are down-weighted so taste reflects preference, not ubiquity).
  style  : cosine of per-chart z-score vectors (how strong A is on a chart *relative
           to other holders* — i.e. an achievement residual, since within one chart
           skill_value is monotonic in achievement). Captures performance pattern.
  latent : cosine of 32-d TruncatedSVD embeddings of the z vectors (denoised; shown
           as a secondary signal — at this scale/sparsity it mostly echoes the above).

Composite (for ranking only) = 0.55*max(style,0) + 0.45*taste. Every result also
returns the breakdown plus the raw drumSkillPoint gap and a confidence based on how
many charts the two players share (small overlaps → low confidence).

Survivorship handling: absent charts are never treated as 0 performance — they simply
do not contribute. Skill-up picks must clear the player's HOT/OTHER 25th-place cutoff
by a margin, and are down-weighted when supporting evidence is thin.
"""

import json
import math
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_BASE = os.environ.get("GD_DATA_DIR") or os.path.join(ROOT, "data")
PROC_DIR = os.path.join(DATA_BASE, "processed")

DIFF_FULL = {"BAS": "BASIC", "ADV": "ADVANCED", "EXT": "EXTREME", "MAS": "MASTER"}


def _safe_norm(mat, axis=1):
    n = np.linalg.norm(mat, axis=axis, keepdims=True)
    n[n == 0] = 1.0
    return mat / n


class Engine:
    def __init__(self, version="galaxywave_delta"):
        self.version = version
        d = os.path.join(PROC_DIR, version)
        with open(os.path.join(d, "charts.json"), encoding="utf-8") as f:
            self.charts = json.load(f)
        with open(os.path.join(d, "players.json"), encoding="utf-8") as f:
            self.players = json.load(f)
        with open(os.path.join(d, "kasegi.json"), encoding="utf-8") as f:
            self.kasegi = json.load(f)
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            self.meta = json.load(f)

        npz = np.load(os.path.join(d, "matrix.npz"))
        self.skill = npz["skill"]
        self.presence = npz["presence"]
        self.presf = self.presence.astype(np.float32)
        self.ach = npz["ach"]
        self.levels = npz["levels"]
        self.pool_is_hot = npz["pool_is_hot"]
        self.chart_count = npz["chart_count"]
        self.chart_mean = npz["chart_mean"]
        self.chart_std = npz["chart_std"]
        self.support_mask = npz["support_mask"].astype(bool)
        self.emb = npz["emb"]
        self.P, self.C = self.skill.shape

        # per-chart z-scored, L2-normalised player matrix (style signal)
        pres = self.presence > 0
        z = np.zeros_like(self.skill)
        idx = np.where(pres)
        z[idx] = (self.skill[idx] - self.chart_mean[idx[1]]) / self.chart_std[idx[1]]
        z[:, ~self.support_mask] = 0.0
        self.zn = _safe_norm(z, axis=1).astype(np.float32)

        # IDF weights for taste (down-weight ubiquitous charts)
        self.idf = np.log((self.P + 1.0) / (self.chart_count + 1.0)).astype(np.float32)
        self.idf[~self.support_mask] = 0.0
        self.presw = self.presf * self.idf          # P x C  weighted presence
        self.wrow = self.presw.sum(axis=1)          # P  weighted sheet "mass"

        self.sp = np.array([p["sp"] for p in self.players], dtype=np.float32)

        self._by_lower = {}
        for p in self.players:
            self._by_lower.setdefault((p["name"] or "").lower(), []).append(p["id"])

        self.kasegi_by_scope = {}
        for k in self.kasegi:
            m = {}
            for pool in ("hot", "other"):
                for rec in (k.get(pool) or []):
                    m[(rec["name"], rec["diff"])] = rec
            self.kasegi_by_scope[k["scope"]] = m

    # ---------------- lookups ----------------
    def find_players(self, query, limit=25):
        q = (query or "").strip().lower()
        if not q:
            return []
        exact = [pid for name, ids in self._by_lower.items() if name == q for pid in ids]
        contains = [p["id"] for p in self.players
                    if q in (p["name"] or "").lower() and p["id"] not in exact]
        ids = sorted(exact + contains, key=lambda i: -self.sp[i])[:limit]
        return [self._brief(i) for i in ids]

    def top_players(self, limit=60):
        ids = sorted(range(self.P), key=lambda i: -self.sp[i])[:limit]
        return [self._brief(i) for i in ids]

    def _brief(self, i):
        p = self.players[i]
        return {
            "id": i, "playerId": p["playerId"], "name": p["name"], "sp": p["sp"],
            "hotPoint": p["hotPoint"], "otherPoint": p["otherPoint"],
            "updateDate": p["updateDate"], "gsvUrl": self._gsv_url(p["playerId"]),
        }

    def _gsv_url(self, player_id):
        return f"http://gsv.fun/en/{self.version}/{player_id}/d"

    # ---------------- similarity signals ----------------
    def _signals(self, i):
        style = self.zn @ self.zn[i]                       # [-1,1]
        latent = self.emb @ self.emb[i]                    # [-1,1]
        inter = self.presw @ self.presw[i] / np.maximum(self.idf @ self.idf, 1e-6)
        # weighted Jaccard taste: |i∩j|_w / |i∪j|_w
        inter_w = self.presf @ (self.presf[i] * self.idf)  # sum idf over shared charts
        union_w = self.wrow + self.wrow[i] - inter_w
        taste = np.divide(inter_w, union_w, out=np.zeros_like(inter_w),
                          where=union_w > 0)
        shared = self.presf @ self.presf[i]
        composite = 0.55 * np.clip(style, 0, None) + 0.45 * taste
        composite[i] = -1
        return {"style": style, "latent": latent, "taste": taste,
                "shared": shared, "composite": composite}

    @staticmethod
    def _confidence(shared):
        return round(min(1.0, float(shared) / 15.0), 2)

    # ---------------- profile ----------------
    def _weighted_z(self, i, band=300.0):
        """Per-chart z-score of player i, comparing only against holders near i's
        skill level (weighted by SP proximity). This credits 'over-levelling' — doing
        well on a hard chart that mostly stronger players hold no longer reads as weak,
        because it's judged against same-bracket peers, not the whole field.
        Returns (mean, std, weight_sum, near_count) over all charts."""
        sp_i = self.sp[i]
        w = np.exp(-((self.sp - sp_i) / band) ** 2).astype(np.float32)   # P
        W = self.presf * w[:, None]                                       # P x C
        sw = W.sum(axis=0)
        mean = np.divide((self.skill * W).sum(axis=0), sw,
                         out=np.zeros(self.C, np.float32), where=sw > 1e-6)
        ex2 = np.divide((self.skill * self.skill * W).sum(axis=0), sw,
                        out=np.zeros(self.C, np.float32), where=sw > 1e-6)
        std_w = np.sqrt(np.clip(ex2 - mean * mean, 0.0, None))
        # shrink toward global dispersion so thin peer sets don't yield wild z-scores
        std = np.maximum(std_w, np.maximum(0.5 * self.chart_std, 2.0))
        near = self.presence[np.abs(self.sp - sp_i) <= band].sum(axis=0)
        return mean, std, sw, near

    def profile(self, i):
        p = self.players[i]
        held = np.where(self.presence[i] > 0)[0]
        wmean, wstd, wsw, near = self._weighted_z(i)

        def wz(ci):
            return (float((self.skill[i, ci] - wmean[ci]) / wstd[ci])
                    if wsw[ci] > 1e-6 else None)

        rows = [(ci, wz(ci)) for ci in held]
        valid = [(ci, z) for ci, z in rows if z is not None and near[ci] >= 4]
        # signature: strong relative to same-bracket peers
        signature = [self._cd(i, ci, z=z, near=int(near[ci]))
                     for ci, z in sorted(valid, key=lambda r: -r[1])][:12]
        # improve: weak relative to same-bracket peers — actionable practice targets
        improve = [self._cd(i, ci, z=z, near=int(near[ci]))
                   for ci, z in sorted(valid, key=lambda r: r[1]) if z < -0.2][:8]
        hardest = [self._cd(i, ci, z=wz(ci), near=int(near[ci]))
                   for ci in sorted(held, key=lambda ci: -self.levels[ci])[:8]]
        best_ach = [self._cd(i, ci, z=wz(ci), near=int(near[ci]))
                    for ci in sorted(held, key=lambda ci: -self.ach[i, ci])[:8]]

        lv = self.levels[held]
        ach = self.ach[i, held]
        rank = int((self.sp > p["sp"]).sum()) + 1

        # level distribution binned to GITADORA 0.1 tiers (floor, never rounded up:
        # 9.95 stays in the 9.9 tier, not 10.0 — 9.9x is its own classification).
        # bin to half-levels (0.5) for a clean distribution; 9.9x kept as its own tier.
        hist = {}
        for v in lv:
            fv = round(float(v), 2)          # avoid float32 noise (9.9 ≈ 9.8999996)
            b = 9.9 if fv >= 9.9 else math.floor(fv * 2 + 1e-6) / 2
            key = round(b, 1)
            hist[key] = hist.get(key, 0) + 1
        level_hist = {f"{k:.1f}": hist[k] for k in sorted(hist)}

        # signature vs efficient split: signature = high personal z & lower popularity;
        # efficient = high popularity / appears in your bracket's kasegi
        kscope = self._kasegi_scope_for(p["sp"])
        kmap = self.kasegi_by_scope.get(kscope, {})
        efficient = [self._cd(i, ci, z=wz(ci), near=int(near[ci])) for ci in
                     sorted(held, key=lambda ci: -self.chart_count[ci])
                     if (self.charts[ci]["name"], self.charts[ci]["diff"]) in kmap][:8]

        # full skill sheet split by pool, sorted by skill desc — feeds the pad grid
        hot_ids = [ci for ci in held if self.pool_is_hot[ci]]
        other_ids = [ci for ci in held if not self.pool_is_hot[ci]]
        sheet_hot = [self._cd(i, ci, z=wz(ci), near=int(near[ci]))
                     for ci in sorted(hot_ids, key=lambda ci: -self.skill[i, ci])]
        sheet_other = [self._cd(i, ci, z=wz(ci), near=int(near[ci]))
                       for ci in sorted(other_ids, key=lambda ci: -self.skill[i, ci])]

        return {
            "player": self._brief(i),
            "rank": rank, "totalPlayers": self.P,
            "percentile": round(100.0 * (1 - rank / self.P), 1),
            "hotPoint": p["hotPoint"], "otherPoint": p["otherPoint"],
            "hotCutoff": p["hotCutoff"], "otherCutoff": p["otherCutoff"],
            "hotCount": p["hotCount"], "otherCount": p["otherCount"],
            "maxLevel": round(float(lv.max()), 2) if len(lv) else 0,
            "avgLevel": round(float(lv.mean()), 2) if len(lv) else 0,
            "avgAch": round(float(ach.mean()), 4) if len(ach) else 0,
            "levelHist": level_hist,
            "signature": signature, "improve": improve, "hardest": hardest,
            "bestAchievements": best_ach, "efficient": efficient,
            "sheetHot": sheet_hot, "sheetOther": sheet_other,
            "kasegiScope": kscope, "compareBand": 300,
        }

    def _cd(self, i, ci, z=None, near=None):
        """Chart detail. `z` is the bracket-relative z-score (vs same-level peers);
        `near` is how many same-bracket players hold this chart (reliability of z)."""
        c = self.charts[ci]
        present = bool(self.presence[i, ci])
        return {
            "id": int(ci), "name": c["name"], "diff": c["diff"],
            "diffFull": DIFF_FULL.get(c["diff"], c["diff"]),
            "level": c["level"], "pool": c["pool"], "count": int(c["count"]),
            "skill": round(float(self.skill[i, ci]), 2) if present else None,
            "ach": round(float(self.ach[i, ci]), 4) if present else None,
            "z": round(z, 2) if z is not None else None,
            "nearPeers": near,
            "chartSkillMean": c["skill_mean"], "chartAchMean": c["ach_mean"],
        }

    # ---------------- single-chart, same-bracket context ----------------
    def chart_detail(self, i, ci, band=300.0):
        """How players near i's skill level perform on one chart — for the pad
        click-through. Returns hold-rate, achievement distribution, i's percentile
        among same-bracket holders, and example peers (exact source precision)."""
        ci = int(ci)
        c = self.charts[ci]
        sp_i = self.sp[i]
        col_pres = self.presence[:, ci] > 0
        nearmask = np.abs(self.sp - sp_i) <= band
        near_holders = np.where(col_pres & nearmask)[0]
        all_holders = np.where(col_pres)[0]
        bracket_size = int(nearmask.sum())

        you_present = bool(self.presence[i, ci])
        you_ach = round(float(self.ach[i, ci]), 4) if you_present else None
        you_skill = round(float(self.skill[i, ci]), 2) if you_present else None

        def dist(idxs):
            if len(idxs) == 0:
                return None
            a = self.ach[idxs, ci].astype(float)
            s = self.skill[idxs, ci].astype(float)
            return {
                "n": int(len(idxs)),
                "achMean": round(float(a.mean()), 4), "achMedian": round(float(np.median(a)), 4),
                "achMin": round(float(a.min()), 4), "achMax": round(float(a.max()), 4),
                "skillMean": round(float(s.mean()), 2), "skillMedian": round(float(np.median(s)), 2),
            }

        near_stats = dist(near_holders)
        all_stats = dist(all_holders)

        # achievement histogram among same-bracket holders, 1% bins (no rounding of
        # the underlying values — this is a distribution, bins are floor(ach%)).
        hist = {}
        for a in self.ach[near_holders, ci]:
            b = int(math.floor(float(a) * 100))
            hist[b] = hist.get(b, 0) + 1
        ach_hist = [{"pct": k, "n": hist[k]} for k in sorted(hist)]

        your_ach_pct = your_skill_pct = None
        if you_present and len(near_holders):
            an = self.ach[near_holders, ci].astype(float)
            sn = self.skill[near_holders, ci].astype(float)
            your_ach_pct = round(float((an < you_ach).mean()) * 100, 1)
            your_skill_pct = round(float((sn < you_skill).mean()) * 100, 1)

        peer_ids = sorted(near_holders, key=lambda j: -self.skill[j, ci])[:14]
        if you_present and i not in peer_ids:
            peer_ids.append(i)
        peers = []
        for j in peer_ids:
            j = int(j)
            peers.append({
                "id": j,
                "name": self.players[j]["name"], "sp": self.players[j]["sp"],
                "ach": round(float(self.ach[j, ci]), 4),
                "skill": round(float(self.skill[j, ci]), 2),
                "gsvUrl": self._gsv_url(self.players[j]["playerId"]),
                "isYou": j == i,
            })
        peers.sort(key=lambda p: -p["skill"])

        return {
            "chart": {
                "id": ci, "name": c["name"], "diff": c["diff"],
                "diffFull": DIFF_FULL.get(c["diff"], c["diff"]),
                "level": c["level"], "pool": c["pool"], "globalHolders": int(c["count"]),
            },
            "you": {"present": you_present, "ach": you_ach, "skill": you_skill,
                    "achPercentile": your_ach_pct, "skillPercentile": your_skill_pct},
            "band": band, "bracketSize": bracket_size,
            "nearHolders": int(len(near_holders)),
            "holdRate": round(len(near_holders) / bracket_size, 4) if bracket_size else 0,
            "near": near_stats, "global": all_stats,
            "achHist": ach_hist, "peers": peers,
        }

    # ---------------- similar players ----------------
    def similar_players(self, i, k=12):
        s = self._signals(i)
        order = np.argsort(-s["composite"])[: k * 3]
        out = []
        for j in order:
            j = int(j)
            if s["composite"][j] <= 0:
                continue
            b = self._brief(j)
            b.update({
                "composite": round(float(s["composite"][j]), 3),
                "taste": round(float(s["taste"][j]), 3),
                "style": round(float(s["style"][j]), 3),
                "latent": round(float(s["latent"][j]), 3),
                "sharedCharts": int(s["shared"][j]),
                "spGap": round(float(self.sp[j] - self.sp[i]), 2),
                "confidence": self._confidence(s["shared"][j]),
                "sharedHighlights": self._pair_shared(i, j, 4),
                "learnFrom": self._learn_from(i, j, 4),
            })
            out.append(b)
            if len(out) >= k:
                break
        return out

    def _pair_shared(self, i, j, n):
        both = np.where((self.presence[i] > 0) & (self.presence[j] > 0)
                        & self.support_mask)[0]
        both = sorted(both, key=lambda ci: -(self.skill[i, ci] + self.skill[j, ci]))[:n]
        return [{"name": self.charts[ci]["name"], "diff": self.charts[ci]["diff"],
                 "level": self.charts[ci]["level"],
                 "yours": round(float(self.skill[i, ci]), 2),
                 "theirs": round(float(self.skill[j, ci]), 2)} for ci in both]

    def _learn_from(self, i, j, n):
        """Charts the other holds that A lacks, split by pool (what A could pick up)."""
        only_j = np.where((self.presence[j] > 0) & (self.presence[i] == 0))[0]
        only_j = sorted(only_j, key=lambda ci: -self.skill[j, ci])[:n]
        return [{"name": self.charts[ci]["name"], "diff": self.charts[ci]["diff"],
                 "level": self.charts[ci]["level"], "pool": self.charts[ci]["pool"],
                 "theirs": round(float(self.skill[j, ci]), 2)} for ci in only_j]

    # ---------------- rivals ----------------
    def rivals(self, i, k=10, window=500.0):
        s = self._signals(i)
        gap = self.sp - self.sp[i]
        prox = np.exp(-(gap / window) ** 2)
        affinity = np.clip(s["style"], 0, None) * 0.55 + s["taste"] * 0.45
        score = affinity * prox
        score[s["shared"] < 3] *= 0.25
        score[i] = -1
        order = np.argsort(-score)[: k * 4]
        out = []
        for j in order:
            j = int(j)
            if score[j] <= 0:
                continue
            g = float(gap[j])
            if g > window * 1.6:
                category = "aspirational"
            elif g > 80:
                category = "growth"
            elif g < -80:
                category = "chaser"   # slightly below, validates your strategy
            else:
                category = "peer"
            b = self._brief(j)
            b.update({
                "rivalScore": round(float(score[j]), 3),
                "category": category,
                "taste": round(float(s["taste"][j]), 3),
                "style": round(float(s["style"][j]), 3),
                "spGap": round(g, 2),
                "sharedCharts": int(s["shared"][j]),
                "confidence": self._confidence(s["shared"][j]),
                "reason": self._rival_reason(g, s["style"][j], s["taste"][j],
                                             int(s["shared"][j]), category),
                "youWin": self._h2h(i, j, True, 3),
                "theyWin": self._h2h(i, j, False, 3),
            })
            out.append(b)
            if len(out) >= k:
                break
        # ensure a mix: at least some chasers/peers if available
        return out

    def _rival_reason(self, gap, style, taste, shared, category):
        label = {"peer": "實力相當的同級對手", "growth": "略強、值得追趕的成長目標",
                 "chaser": "略弱、會追著你跑的對手", "aspirational": "更高段位的憧憬目標"}[category]
        return (f"{label}：風格 {style:.2f}・口味 {taste:.2f}・共同曲 {shared} 首・"
                f"總分差 {gap:+.2f}")

    def _h2h(self, i, j, win, n):
        both = np.where((self.presence[i] > 0) & (self.presence[j] > 0))[0]
        if len(both) == 0:
            return []
        diff = self.skill[i, both] - self.skill[j, both]
        order = both[np.argsort(-diff if win else diff)]
        res = []
        for ci in order[:n]:
            d = float(self.skill[i, ci] - self.skill[j, ci])
            if (win and d <= 0) or (not win and d >= 0):
                continue
            res.append({"name": self.charts[ci]["name"], "diff": self.charts[ci]["diff"],
                        "level": self.charts[ci]["level"],
                        "yours": round(float(self.skill[i, ci]), 2),
                        "theirs": round(float(self.skill[j, ci]), 2),
                        "delta": round(d, 2)})
        return res

    # ---------------- song recommendations ----------------
    def _kasegi_scope_for(self, sp):
        scopes = sorted(self.kasegi_by_scope.keys())
        chosen = scopes[0] if scopes else 0
        for s in scopes:
            if sp >= s:
                chosen = s
        return chosen

    def _expected_ach(self, i, ci, kmap):
        """Blend neighbour-of-similar-level achievement, kasegi bracket prior, and
        the chart's global mean. Returns (estimate, confidence 0..1)."""
        sp_i = self.sp[i]
        holders = np.where(self.presence[:, ci] > 0)[0]
        parts, weights = [], []
        if len(holders):
            d = np.abs(self.sp[holders] - sp_i)
            near = holders[d <= 500]
            if len(near) >= 3:
                w = np.exp(-(np.abs(self.sp[near] - sp_i) / 350.0) ** 2)
                parts.append(float((self.ach[near, ci] * w).sum() / max(w.sum(), 1e-6)))
                weights.append(min(1.0, len(near) / 12.0) * 1.0)
        krec = kmap.get((self.charts[ci]["name"], self.charts[ci]["diff"]))
        if krec and krec.get("averageSkill") and self.levels[ci] > 0:
            ka = float(krec["averageSkill"]) / (self.levels[ci] * 20.0)
            parts.append(min(ka, 1.0))
            weights.append(min(1.0, float(krec.get("count", 0)) / 20.0) * 0.8)
        gm = float(self.charts[ci]["ach_mean"]) or 0.7
        parts.append(gm)
        weights.append(0.3)
        w = np.array(weights)
        est = float(np.average(parts, weights=w))
        conf = round(min(1.0, float(w[:-1].sum())), 2)  # exclude the weak global prior
        return est, conf

    def song_recs(self, i, k=15, neighbors=30):
        s = self._signals(i)
        nbr = [int(j) for j in np.argsort(-s["composite"])[:neighbors]
               if s["composite"][j] > 0]
        sims = np.array([s["composite"][j] for j in nbr], dtype=np.float32)
        wsum = float(sims.sum()) or 1.0

        held = self.presence[i] > 0
        held_lv = self.levels[held]
        my_max_lv = float(held_lv.max()) if held.any() else 9.9
        my_med_lv = float(np.median(held_lv)) if held.any() else 8.0
        p = self.players[i]
        kscope = self._kasegi_scope_for(p["sp"])
        kmap = self.kasegi_by_scope.get(kscope, {})

        # neighbour holders per candidate chart (vectorised)
        nbr_pres = self.presence[nbr]                      # n x C
        wcol = (nbr_pres * sims[:, None]).sum(axis=0)      # weighted holders
        ncol = nbr_pres.sum(axis=0)                        # raw holders

        cand = []
        for ci in range(self.C):
            if held[ci] or not self.support_mask[ci] or ncol[ci] == 0:
                continue
            lv = float(self.levels[ci])
            if lv > my_max_lv + 0.6 or lv < my_med_lv - 2.5:
                continue
            wfrac = float(wcol[ci]) / wsum
            est_ach, est_conf = self._expected_ach(i, ci, kmap)
            est_skill = lv * 20.0 * est_ach
            pool = "hot" if self.pool_is_hot[ci] else "other"
            cutoff = p["hotCutoff"] if pool == "hot" else p["otherCutoff"]
            gain = est_skill - cutoff
            krec = kmap.get((self.charts[ci]["name"], self.charts[ci]["diff"]))
            cand.append({
                "id": int(ci), "name": self.charts[ci]["name"], "diff": self.charts[ci]["diff"],
                "diffFull": DIFF_FULL.get(self.charts[ci]["diff"], self.charts[ci]["diff"]),
                "level": round(lv, 2), "pool": pool, "count": int(self.chart_count[ci]),
                "neighborHolders": int(ncol[ci]), "neighborTotal": len(nbr),
                "neighborFrac": round(wfrac, 3),
                "estAch": round(est_ach, 4), "estSkill": round(est_skill, 2),
                "estConfidence": est_conf,
                "cutoff": round(cutoff, 2), "gain": round(gain, 2),
                "chartAchMean": self.charts[ci]["ach_mean"],
                "inKasegi": bool(krec),
                "kasegiAvgSkill": round(float(krec["averageSkill"]), 2) if krec else None,
                "tags": [],
            })

        # discovery: neighbour taste, regardless of immediate skill gain
        discovery = sorted(cand, key=lambda c: (-c["neighborFrac"], -c["count"]))[:k]
        for c in discovery:
            c["tags"] = self._rec_tags(c, my_med_lv, kind="discovery")
            c["why"] = (f"{c['neighborHolders']}/{c['neighborTotal']} 位與你最相似的玩家"
                        f"都在玩這首（Lv{c['level']:.2f}）"
                        + ("，且是你分數段的熱門賺分曲" if c["inKasegi"] else ""))

        # skill-up: must clear cutoff by a real margin, with evidence
        margin = 2.0
        skillup = [c for c in cand
                   if c["gain"] > margin and 0.55 <= c["estAch"] <= 0.999
                   and c["estConfidence"] >= 0.3]
        for c in skillup:
            # confidence-discounted expected gain for ranking
            c["adjGain"] = round(c["gain"] * (0.4 + 0.6 * c["estConfidence"]), 2)
        skillup = sorted(skillup, key=lambda c: -c["adjGain"])[:k]
        for c in skillup:
            c["tags"] = self._rec_tags(c, my_med_lv, kind="skillup")
            c["why"] = (f"同級玩家平均達成率估 {c['estAch']*100:.2f}% → 單曲 skill ≈ "
                        f"{c['estSkill']:.2f}，高於你 {c['pool'].upper()} 門檻 "
                        f"{c['cutoff']:.2f}，總分可 +{c['gain']:.2f}（信心 {c['estConfidence']:.0%}）")

        # combined: charts that are BOTH liked by similar players AND raise your total.
        # Computed over the full candidate pool (not the truncated lists) so the
        # sweet-spot picks are not lost when the two top-K lists barely overlap.
        combined = []
        for c in cand:
            if (c["gain"] > margin and c["neighborFrac"] >= 0.2
                    and 0.55 <= c["estAch"] <= 0.999 and c["estConfidence"] >= 0.3):
                cc = dict(c)
                cc["adjGain"] = round(c["gain"] * (0.4 + 0.6 * c["estConfidence"]), 2)
                cc["tags"] = self._rec_tags(cc, my_med_lv, kind="skillup")
                cc["why"] = ("相似玩家愛玩 × 能提升總分 — "
                             f"{cc['neighborHolders']}/{cc['neighborTotal']} 位相似玩家在玩，"
                             f"估達成 {cc['estAch']*100:.2f}% 可為 {cc['pool'].upper()} +{cc['gain']:.2f}")
                combined.append(cc)
        combined = sorted(combined,
                          key=lambda c: -(c["adjGain"] * (0.5 + c["neighborFrac"])))[:k]

        return {"kasegiScope": kscope, "myMaxLevel": round(my_max_lv, 2),
                "myMedianLevel": round(my_med_lv, 2),
                "discovery": discovery, "skillUp": skillup, "combined": combined}

    def _rec_tags(self, c, med_lv, kind):
        # stable keys; the frontend localises them (see i18n.js tag.*)
        tags = []
        if c["neighborFrac"] >= 0.4:
            tags.append("popular")
        if c.get("gain", 0) > 8:
            tags.append("highGain")
        if c["inKasegi"]:
            tags.append("efficient")
        if c["level"] <= med_lv:
            tags.append("easy")
        elif c["level"] > med_lv + 0.4:
            tags.append("challenge")
        if kind == "skillup" and c.get("estConfidence", 0) >= 0.7:
            tags.append("confident")
        return tags


_engine_cache = {}


def get_engine(version="galaxywave_delta"):
    if version not in _engine_cache:
        _engine_cache[version] = Engine(version)
    return _engine_cache[version]
