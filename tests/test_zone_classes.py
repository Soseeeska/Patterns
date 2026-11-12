import numpy as np

from tpo.zone_classes import RANGE, RESISTANCE, SUPPORT, classify, interaction_types


def test_types():
    # зона 100..110
    o = np.array([120, 90, 105, 120, 90, 200])
    h = np.array([121, 105, 112, 121, 115, 201])
    l = np.array([108, 89, 104, 95, 89, 199])
    c = np.array([118, 92, 106, 96, 113, 200])
    t = interaction_types(o, h, l, c, 100, 110, 1)
    assert list(t) == ["long", "short", "range", "range", "range", ""]


def test_classify_majority_and_tie():
    assert classify(np.array(["long", "long", "range"])) == SUPPORT
    assert classify(np.array(["short", "short", "long"])) == RESISTANCE
    assert classify(np.array(["long", "short"])) == RANGE


def test_classify_by_rejection_share():
    t = np.array(["long"] * 6 + ["short"] * 3 + ["range"] * 20)
    assert classify(t, 0.6) == SUPPORT
    assert classify(t, 0.7) == RANGE
