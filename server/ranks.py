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
