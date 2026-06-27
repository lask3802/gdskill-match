"""
overlay.py — build a per-chart observation overlay from a single player's
uploaded full-data payload (spec §6).

The overlay is a pure, read-only projection of an upload onto the base chart
catalog. It NEVER mutates matrix.npz or the base per-chart stats; the engine
injects this overlay only when handling the owning (or a public) player.

Each base chart `ci` gets one observation:

  obs_kind: 0 unknown / 1 gsv_exact / 2 upload_exact / 3 upload_rank
  played_mask : the player has played this chart (rank != "-")
  eval_mask   : a usable (exact or coarse-rank) skill observation exists
  skill_mean  : level * 20 * achievement   (skill = level*20*ach, spec §2)
  skill_sd    : 0 for exact; level*20*sqrt(var_post) for rank (uncertainty)
  ach_mean    : posterior achievement fraction
  obs_weight  : 1.0 for exact; 0.15..0.65 for rank (band width, spec §6.1)

Charts not present in the base catalog (`chart_index`) are kept in
`Overlay.extra` (counted, but never placed in the C-sized arrays).
"""

from dataclasses import dataclass, field

import numpy as np

from server import ranks

# obs_kind integer codes (spec §6).
KIND_UNKNOWN = 0
KIND_GSV_EXACT = 1
KIND_UPLOAD_EXACT = 2
KIND_UPLOAD_RANK = 3


@dataclass
class Overlay:
    """Per-chart observation arrays sized to the base catalog (length C)."""

    played_mask: np.ndarray
    eval_mask: np.ndarray
    skill_mean: np.ndarray
    skill_sd: np.ndarray
    ach_mean: np.ndarray
    obs_weight: np.ndarray
    obs_kind: np.ndarray
    extra: list = field(default_factory=list)


def build_overlay(latest, chart_index, charts, levels, ctx_fn):
    """Project an upload's `latest["charts"]` onto the base chart catalog.

    Args:
      latest:      the stored latest-overlay dict (`{"charts": [...], ...}`).
      chart_index: maps `(name, diff) -> ci` for base catalog charts.
      charts:      the base catalog list (used only for its length C).
      levels:      length-C array of canonical chart levels (skill basis).
      ctx_fn:      `ci -> (mu_ctx, var_ctx)` context prior for rank-only obs.

    Returns an `Overlay`. Unknown charts go to `Overlay.extra`.
    """
    n = len(charts)
    played_mask = np.zeros(n, dtype=bool)
    eval_mask = np.zeros(n, dtype=bool)
    skill_mean = np.zeros(n, dtype=np.float64)
    skill_sd = np.zeros(n, dtype=np.float64)
    ach_mean = np.zeros(n, dtype=np.float64)
    obs_weight = np.zeros(n, dtype=np.float64)
    obs_kind = np.zeros(n, dtype=np.int8)
    extra = []

    for ch in (latest or {}).get("charts", []):
        key = (ch.get("name"), ch.get("diff"))
        ci = chart_index.get(key)
        if ci is None:
            # Unknown to the base catalog (e.g. a chart added after the last
            # base rebuild): count it but keep it out of the C-sized arrays.
            extra.append(ch)
            continue

        rank = ch.get("rank")
        if rank == "-":
            # Present in the payload but never played: not an observation.
            continue

        played_mask[ci] = True
        level = float(levels[ci])
        ach = ch.get("achievement")

        if ch.get("exact") and ach is not None:
            ach = float(ach)
            skill_mean[ci] = level * 20.0 * ach
            skill_sd[ci] = 0.0
            ach_mean[ci] = ach
            obs_weight[ci] = 1.0
            obs_kind[ci] = KIND_UPLOAD_EXACT
            eval_mask[ci] = True
        elif rank in ranks.RANK_BANDS:
            mu_ctx, var_ctx = ctx_fn(ci)
            obs = ranks.obs_from_rank(rank, level, mu_ctx, var_ctx)
            skill_mean[ci] = obs["skill_mean"]
            skill_sd[ci] = obs["skill_sd"]
            ach_mean[ci] = obs["mu_ach"]
            obs_weight[ci] = obs["weight"]
            obs_kind[ci] = KIND_UPLOAD_RANK
            eval_mask[ci] = True
        # else: played but no usable rank/exact obs -> unknown (kind stays 0).

    return Overlay(
        played_mask=played_mask,
        eval_mask=eval_mask,
        skill_mean=skill_mean,
        skill_sd=skill_sd,
        ach_mean=ach_mean,
        obs_weight=obs_weight,
        obs_kind=obs_kind,
        extra=extra,
    )
