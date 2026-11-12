from tpo.hvn_zones import Zone, merge_zones, overlap_share


def test_overlap_share_uses_narrower_zone():
    assert overlap_share(Zone(0, 9), Zone(5, 14)) == 0.5
    assert overlap_share(Zone(0, 39), Zone(10, 13)) == 1.0
    assert overlap_share(Zone(0, 3), Zone(5, 8)) == 0.0


def test_merge_when_overlap_and_width_ok():
    out = merge_zones([Zone(0, 9, [1]), Zone(5, 14, [2])], 0.5, 40)
    assert [(z.lo, z.hi, z.dates) for z in out] == [(0, 14, [1, 2])]


def test_no_merge_below_overlap():
    out = merge_zones([Zone(0, 9, [1]), Zone(6, 15, [2])], 0.5, 40)
    assert len(out) == 2


def test_no_merge_if_union_too_wide():
    out = merge_zones([Zone(0, 30, [1]), Zone(20, 45, [2])], 0.5, 40)
    assert len(out) == 2


def test_cascade_merge():
    # 0..9 и 20..29 не пересекаются; зона 5..24 сливается с обеими
    out = merge_zones([Zone(0, 9, [1]), Zone(20, 29, [2]), Zone(5, 24, [3])], 0.5, 40)
    assert [(z.lo, z.hi, z.dates) for z in out] == [(0, 29, [1, 2, 3])]


def test_roles_from_width_blocked_pairs():
    from tpo.hvn_zones import BOTH, RANGE, RESISTANCE, SUPPORT, zone_roles
    zones = merge_zones([Zone(0, 19, [1]), Zone(10, 29, [2]), Zone(100, 105, [3]),
                         Zone(20, 39, [4])], 0.5, 25)
    roles, _, _ = zone_roles(zones, 0.5)
    by_lo = {z.lo: r for z, r in zip(zones, roles)}
    assert by_lo == {0: SUPPORT, 10: BOTH, 20: RESISTANCE, 100: RANGE}


def test_clusters_follow_blocked_pairs():
    from tpo.hvn_zones import zone_clusters
    zones = [Zone(0, 19), Zone(10, 29), Zone(20, 39), Zone(100, 105)]
    assert zone_clusters(zones, 0.5) == [0, 0, 0, 3]
