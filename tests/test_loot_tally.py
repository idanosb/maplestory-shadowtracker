import json

from expstrip.loot_tally import load_tally, save_tally


def test_tally_round_trip(tmp_path):
    p = tmp_path / "sub" / "loot_tally.json"
    save_tally(p, 1234)
    assert json.loads(p.read_text(encoding="utf-8")) == {"unsold_trash": 1234, "last_exact": None}
    assert load_tally(p) == 1234


def test_tally_missing_or_broken_is_zero(tmp_path):
    p = tmp_path / "t.json"
    assert load_tally(p) == 0
    for bad in ["not json", "[]", '{"unsold_trash": "x"}', '{"unsold_trash": -5}',
                '{"unsold_trash": 1.5}', '{"unsold_trash": true}', '{}']:
        p.write_text(bad, encoding="utf-8")
        assert load_tally(p) == 0
    p.write_bytes(b"\xff\xfe\x00")
    assert load_tally(p) == 0


def test_tally_state_round_trip_and_old_format(tmp_path):
    from expstrip.loot_tally import load_tally_state
    p = tmp_path / "t.json"
    save_tally(p, 10, 5000)
    assert load_tally_state(p) == (10, 5000)
    save_tally(p, 10)
    assert load_tally_state(p) == (10, None)
    p.write_text('{"unsold_trash": 77}', encoding="utf-8")
    assert load_tally_state(p) == (77, None)
    assert load_tally(p) == 77


def test_tally_state_rejects_bad_last_exact(tmp_path):
    from expstrip.loot_tally import load_tally_state
    p = tmp_path / "t.json"
    for bad in ("true", "-1", '"x"', "1.5"):
        p.write_text('{"unsold_trash": 3, "last_exact": %s}' % bad, encoding="utf-8")
        assert load_tally_state(p) == (3, None)
