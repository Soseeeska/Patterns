import numpy as np

from tpo.zone_align import align_zone, edge_scores


def test_edge_scores_prefers_body_edge_with_wick():
    # три бара: тело заканчивается на 100, хвост выше до 103
    o = np.array([98.0, 97.0, 99.0])
    c = np.array([100.0, 100.0, 100.0])
    h = np.array([103.0, 102.0, 101.5])
    l = np.array([97.0, 96.0, 98.5])
    grid = np.array([99.0, 100.0, 101.0])
    s = edge_scores(o, h, l, c, grid, 0.25, 1.0, 0.5)
    assert s[1] == 3 and s[0] == 0.5 and s[2] == 0


def test_align_moves_top_to_cluster_and_keeps_bottom():
    o = np.array([98.0, 97.0, 99.0])
    c = np.array([100.0, 100.0, 100.0])
    h = np.array([103.0, 102.0, 101.5])
    l = np.array([97.0, 96.0, 98.5])
    lo, hi, s_lo, s_hi = align_zone(o, h, l, c, 90.0, 101.0, 0.25, 2.0, 1.0, 0.5, 2.0, 0.5, 40.0)
    assert hi == 100.0 and lo == 90.0 and s_hi == 3
