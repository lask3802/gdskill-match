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
