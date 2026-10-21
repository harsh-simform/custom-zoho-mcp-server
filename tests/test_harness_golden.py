"""Seam: `assert_golden(name, text)` and the `--update-goldens` pytest option (tests/support/golden.py,
tests/conftest.py). Goldens live under tests/golden/<name>; a bare name gets `.txt`."""
from pathlib import Path

import pytest
from support.golden import assert_golden

pytest_plugins = ["pytester"]


def test_matching_text_passes(tmp_path):
    (tmp_path / "a.txt").write_text("line one\nline two\n", encoding="utf-8")
    assert_golden("a", "line one\nline two", root=tmp_path, update=False)


def test_trailing_newlines_are_not_significant(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    assert_golden("a", "hello\n\n", root=tmp_path, update=False)


def test_an_explicit_extension_is_kept(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "out.json").write_text('{"a": 1}\n', encoding="utf-8")
    assert_golden("sub/out.json", '{"a": 1}', root=tmp_path, update=False)


def test_mismatch_fails_with_a_diff_and_the_update_hint(tmp_path):
    (tmp_path / "a.txt").write_text("status: Open\n", encoding="utf-8")
    with pytest.raises(AssertionError) as excinfo:
        assert_golden("a", "status: Closed", root=tmp_path, update=False)
    message = str(excinfo.value)
    assert "-status: Open" in message
    assert "+status: Closed" in message
    assert "--update-goldens" in message


def test_missing_golden_fails_instead_of_silently_creating_it(tmp_path):
    with pytest.raises(AssertionError, match="--update-goldens"):
        assert_golden("new", "text", root=tmp_path, update=False)
    assert not (tmp_path / "new.txt").exists()


def test_update_writes_the_golden_with_one_trailing_newline_and_creates_directories(tmp_path):
    assert_golden("deep/dir/new", "fresh output", root=tmp_path, update=True)
    assert (tmp_path / "deep" / "dir" / "new.txt").read_bytes() == b"fresh output\n"


def test_update_overwrites_a_stale_golden(tmp_path):
    (tmp_path / "a.txt").write_text("old\n", encoding="utf-8")
    assert_golden("a", "new", root=tmp_path, update=True)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new\n"


@pytest.mark.parametrize("bad", ["../escape", "/abs/path", "a/../../b"])
def test_names_cannot_escape_the_golden_root(tmp_path, bad):
    with pytest.raises(ValueError):
        assert_golden(bad, "x", root=tmp_path, update=True)


def test_update_goldens_flag_is_wired_through_pytest(pytester, monkeypatch):
    """End to end through the real option and the project's own conftest: without the flag a missing
    golden fails; with it the file appears and the test passes."""
    tests_dir = Path(__file__).parent
    monkeypatch.setenv("PYTHONPATH", str(tests_dir))
    pytester.makeconftest((tests_dir / "conftest.py").read_text(encoding="utf-8"))
    pytester.makepyfile(
        test_sample="""
        from pathlib import Path
        from support.golden import assert_golden

        def test_it():
            assert_golden("sample", "hello", root=Path(__file__).parent / "golden")
        """
    )
    pytester.runpytest_subprocess().assert_outcomes(failed=1)

    pytester.runpytest_subprocess("--update-goldens").assert_outcomes(passed=1)
    assert (pytester.path / "golden" / "sample.txt").read_text(encoding="utf-8") == "hello\n"

    pytester.runpytest_subprocess().assert_outcomes(passed=1)
