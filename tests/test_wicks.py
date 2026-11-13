import numpy as np

from tpo.wicks import BOUNDARY, INSIDE, OUTSIDE, main_opposite, wick_positions


def _one(o, h, l, c, lo=100, hi=110, mw=1):
    lw, uw = wick_positions(np.array([o]), np.array([h]), np.array([l]), np.array([c]), lo, hi, mw)
    return int(lw[0]), int(uw[0])


def test_lower_wick_crosses_top_boundary():
    assert _one(120, 121, 108, 115) == (BOUNDARY, OUTSIDE)


def test_lower_wick_through_whole_zone():
    assert _one(120, 121, 95, 115)[0] == BOUNDARY


def test_wick_inside_zone():
    assert _one(108, 112, 102, 109) == (INSIDE, BOUNDARY)    # нижний 102..108 внутри, верхний 109..112 через hi
    assert _one(108, 109.5, 102, 109, mw=0.5)[1] == INSIDE


def test_upper_wick_crosses_bottom_boundary():
    assert _one(90, 103, 89.5, 95)[1] == BOUNDARY


def test_main_is_lower_for_bear_upper_for_bull():
    o, c = np.array([10, 5]), np.array([5, 10])
    main, opp, doji = main_opposite(o, c, np.array([1, 2]), np.array([3, 1]))
    assert list(main) == [1, 1] and list(opp) == [3, 2] and not doji.any()
