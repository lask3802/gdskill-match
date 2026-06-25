# tests/test_overlay.py
import numpy as np
from server import overlay

def fixt():
    charts = [{"name":"X","diff":"MAS","level":8.45},
              {"name":"Y","diff":"EXT","level":6.0},
              {"name":"Z","diff":"ADV","level":4.0}]
    idx = {(c["name"],c["diff"]):i for i,c in enumerate(charts)}
    levels = np.array([8.45,6.0,4.0], dtype=np.float32)
    return charts, idx, levels

def test_exact_and_rank_obs():
    charts, idx, levels = fixt()
    latest = {"charts":[
        {"name":"X","diff":"MAS","rank":"B","achievement":0.6515,"exact":True,"level":8.45},
        {"name":"Y","diff":"EXT","rank":"S","exact":False,"level":6.0},
    ]}
    ov = overlay.build_overlay(latest, idx, charts, levels, ctx_fn=lambda ci:(0.9,0.01))
    assert ov.played_mask[0] and ov.played_mask[1] and not ov.played_mask[2]
    assert ov.obs_kind[0]==2 and ov.skill_sd[0]==0 and ov.obs_weight[0]==1.0
    assert ov.obs_kind[1]==3 and ov.skill_sd[1]>0 and 0.15<=ov.obs_weight[1]<=0.65
    assert abs(ov.skill_mean[0] - 8.45*20*0.6515) < 1e-3

def test_unknown_chart_goes_to_extra():
    charts, idx, levels = fixt()
    latest = {"charts":[{"name":"NEW","diff":"MAS","rank":"A","exact":False,"level":9.0}]}
    ov = overlay.build_overlay(latest, idx, charts, levels, ctx_fn=lambda ci:(0.85,0.01))
    assert len(ov.extra)==1 and ov.played_mask.sum()==0
