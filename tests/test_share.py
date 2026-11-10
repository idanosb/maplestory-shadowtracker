from expstrip.share import MINIMAP, PICKUPS, STATUS_BAR, covered, intersects, read_zones, to_screen
from expstrip.vision.layout import Rect, Regions


def _regions(scale=1.0, minimap=None):
    return Regions(exp=Rect(0, 0, 10, 10), level=None, minimap_title=minimap, scale=scale)


def test_zones_without_regions_is_bottom_band_only():
    zones = read_zones(1000, 800, None)
    assert [n for n, _ in zones] == [STATUS_BAR]
    band = zones[0][1]
    assert band.y + band.h == 800 and band.w == 1000 and band.h == 120


def test_zones_with_regions_add_pickups_and_minimap():
    zones = dict(read_zones(2560, 1440, _regions(1.0, Rect(20, 30, 200, 40))))
    assert set(zones) == {STATUS_BAR, PICKUPS, MINIMAP}
    assert zones[STATUS_BAR].h == 276
    p = zones[PICKUPS]
    assert p.x + p.w == 2558 and p.y + p.h == 1440 - 276   # right edge, just above the band
    assert zones[MINIMAP] == Rect(20, 30, 200, 40)


def test_zones_scale_down_with_ui_scale():
    zones = dict(read_zones(1280, 720, _regions(0.5)))
    assert zones[STATUS_BAR].h == 138


def test_to_screen_offsets_by_window_origin():
    assert to_screen([("a", Rect(1, 2, 3, 4))], 100, 200) == [("a", Rect(101, 202, 3, 4))]


def test_intersects_edges_touching_is_not_overlap():
    assert intersects(Rect(0, 0, 10, 10), Rect(5, 5, 10, 10))
    assert not intersects(Rect(0, 0, 10, 10), Rect(10, 0, 10, 10))
    assert not intersects(Rect(0, 0, 10, 10), Rect(0, 10, 10, 10))


def test_covered_names_in_zone_order():
    zones = [("bottom", Rect(0, 700, 1000, 100)), ("top", Rect(0, 0, 200, 50))]
    assert covered([Rect(400, 0, 640, 48)], zones) == []          # strip top-centre: fine
    assert covered([Rect(100, 20, 640, 48)], zones) == ["top"]
    assert covered([Rect(0, 0, 50, 50), Rect(300, 750, 50, 50)], zones) == ["bottom", "top"]
