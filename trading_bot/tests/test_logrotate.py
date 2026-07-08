"""core.logrotate — bounded growth for append-only JSONL logs."""
from core.logrotate import trim_jsonl


def _lines(path):
    return path.read_text().splitlines()


def test_under_cap_untouched(tmp_path):
    f = tmp_path / "log.jsonl"
    f.write_text("\n".join(f'{{"i":{i}}}' for i in range(100)) + "\n")
    assert trim_jsonl(f, max_lines=200, keep_lines=100) is False
    assert len(_lines(f)) == 100


def test_over_cap_keeps_newest(tmp_path):
    f = tmp_path / "log.jsonl"
    f.write_text("\n".join(f'{{"i":{i}}}' for i in range(300)) + "\n")
    assert trim_jsonl(f, max_lines=200, keep_lines=100) is True
    kept = _lines(f)
    assert len(kept) == 100
    assert kept[0] == '{"i":200}' and kept[-1] == '{"i":299}'   # newest survive


def test_missing_file_is_noop(tmp_path):
    assert trim_jsonl(tmp_path / "nope.jsonl") is False
