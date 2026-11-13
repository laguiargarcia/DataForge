import tempfile
from pathlib import Path

import pathspec
from hypothesis import example, given, strategies as st

from dataforge.deploy.ignore import DeployIgnore


def _mk(ws: Path, rel: str, content: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_missing_file_matches_nothing(tmp_path):
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("scripts/x.py") is False


def test_glob_and_dir_patterns(tmp_path):
    _mk(tmp_path, "deploy/.deployignore",
        "*.duckdb\nraw/\nconnections/connections.yaml\n")
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("cleansed.duckdb") is True
    assert ig.matches("raw/accounts/data.parquet") is True
    assert ig.matches("connections/connections.yaml") is True
    assert ig.matches("scripts/landing/landing2raw.py") is False


def test_negation_supported(tmp_path):
    _mk(tmp_path, "deploy/.deployignore", "config/*\n!config/keep.yaml\n")
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("config/secret.yaml") is True
    assert ig.matches("config/keep.yaml") is False


def test_pycache_ignored_by_default_without_file(tmp_path):
    # No .deployignore at all — Python build artifacts must still be excluded so
    # resolve_pairs's scripts/**/* glob never promotes a .pyc to the target.
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("scripts/cleansed2curated/__pycache__/dim_calendar.cpython-312.pyc") is True
    assert ig.matches("scripts/x.pyc") is True
    assert ig.matches("scripts/x.pyo") is True
    assert ig.matches("scripts/landing/landing2raw.py") is False


def test_pycache_default_survives_custom_file(tmp_path):
    # A workspace .deployignore with unrelated lines doesn't drop the built-in defaults.
    _mk(tmp_path, "deploy/.deployignore", "scripts/report/\n")
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("scripts/cleansed2curated/__pycache__/x.pyc") is True
    assert ig.matches("scripts/report/export_bridge.py") is True


def test_default_pycache_ignore_is_negatable(tmp_path):
    # User file lines apply after defaults, so an explicit re-include still wins.
    _mk(tmp_path, "deploy/.deployignore", "!keep.pyc\n")
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("scripts/x.pyc") is True
    assert ig.matches("keep.pyc") is False


def test_web_build_artifacts_ignored_by_default(tmp_path):
    # df-ve7: apps/** pairs must never promote node dependencies or Playwright output.
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("apps/financas/node_modules/react/index.js") is True
    assert ig.matches("apps/financas/test-results/.last-run.json") is True
    assert ig.matches("apps/financas/dist/index.html") is False
    assert ig.matches("apps/financas/src/App.tsx") is False


# --- df-ahua.23: case-insensitive matching (matches_any_case), encoding, constructor default ---

_NAME = st.text(alphabet="abcXYZé_", min_size=1, max_size=6)


def _recase(draw, text: str) -> str:
    """The same text with every letter's case drawn independently."""
    return "".join(c.upper() if draw(st.booleans()) else c.lower() for c in text)


@st.composite
def _pattern_and_paths(draw):
    """A directory pattern, a file under it spelled as written, and the same file in a drawn case."""
    folder = draw(_NAME)
    leaf = draw(_NAME)
    exact = f"{folder}/{leaf}.txt"
    variant = _recase(draw, exact)
    return folder, exact, variant


@given(_pattern_and_paths())
@example(("config", "config/a.txt", "CONFIG/a.txt"))
@example(("Config", "Config/a.txt", "config/a.txt"))
def test_any_case_match_ignores_case_while_matches_stays_exact(case):
    """matches_any_case finds a path under an ignored folder in any spelling (NTFS, df-886.18);
    matches() keeps git's case-sensitive meaning, so it is true only for the spelling as written."""
    folder, exact, variant = case
    with tempfile.TemporaryDirectory() as d:
        _mk(Path(d), "deploy/.deployignore", "").write_bytes(f"{folder}/\n".encode("utf-8"))
        ig = DeployIgnore.load(Path(d))
    assert ig.matches(exact) is True
    assert ig.matches_any_case(variant) is True
    assert ig.matches(variant) is (variant.split("/")[0] == folder)


@given(st.text(alphabet="abcXYZ/._", min_size=1, max_size=12))
def test_any_case_match_is_never_narrower_than_matches(path):
    """Whatever matches() ignores, matches_any_case ignores too, so prune never keeps less than plan skips."""
    with tempfile.TemporaryDirectory() as d:
        _mk(Path(d), "deploy/.deployignore", "").write_bytes(b"Data/\n*.LOG\n")
        ig = DeployIgnore.load(Path(d))
    if ig.matches(path):
        assert ig.matches_any_case(path) is True


def test_ignore_file_is_read_as_utf8(tmp_path):
    """.deployignore is UTF-8 on every OS: a non-ASCII folder name must match on Windows' cp1252 too."""
    _mk(tmp_path, "deploy/.deployignore", "").write_bytes("relatório/\n".encode("utf-8"))
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("relatório/2026.xlsx") is True


def test_built_from_one_spec_any_case_falls_back_to_it():
    """Without a casefolded spec, matches_any_case still answers, using the given spec."""
    ig = DeployIgnore(pathspec.GitIgnoreSpec.from_lines(["raw/"]))
    assert ig.matches_any_case("raw/x.parquet") is True
    assert ig.matches_any_case("scripts/x.py") is False
