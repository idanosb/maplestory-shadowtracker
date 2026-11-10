from expstrip.config import Settings, load_settings, save_settings


def test_missing_file_gives_defaults_and_writes_it(tmp_path):
    p = tmp_path / "settings.json"
    s = load_settings(p)
    assert s.window_title == "MapleStory" and s.locked is False
    assert p.exists()


def test_corrupt_file_gives_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not json")
    assert load_settings(p).window_title == "MapleStory"


def test_roundtrip(tmp_path):
    p = tmp_path / "settings.json"
    save_settings(Settings(window_title="Maple", strip_x=10, strip_y=20, locked=True), p)
    assert load_settings(p) == Settings("Maple", 10, 20, True)


def test_show_in_share_defaults_off_and_roundtrips(tmp_path):
    p = tmp_path / "settings.json"
    assert load_settings(p).show_in_share is False
    save_settings(Settings(show_in_share=True), p)
    assert load_settings(p).show_in_share is True
