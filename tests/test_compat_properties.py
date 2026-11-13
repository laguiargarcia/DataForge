"""Rules of dataforge/compat.py, the one place that turns names, DATAFORGE_HOME and paths into
filesystem paths and database URLs (docs/subsystems/compat.md)."""
import sys
import tempfile
from pathlib import Path

import platformdirs
import pytest
from hypothesis import example, given, strategies as st
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from dataforge import compat
from dataforge.compat import (
    DEFAULT_API_PORT, allowed_hosts, api_port, apps_dir, curated_dir, data_dir, db_url, global_db_url,
    list_workspaces, resolve_db, resolve_workspace, valid_name,
)
from dataforge.db import init_db

_SAFE_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
_SAFE_NAME = st.text(alphabet=_SAFE_CHARS, min_size=1, max_size=12)
# Names Windows cannot create as a file or folder (df-ahua.23.3): planting them fails on NTFS.
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10))}
_DIR_NAME = _SAFE_NAME.filter(lambda s: s.upper() not in _RESERVED)


# --- valid_name -----------------------------------------------------------------------------------

@given(_SAFE_NAME)
@example("a")
@example("-")
@example("_")
@example("a" * 255)
def test_a_name_from_the_safe_alphabet_is_valid(name):
    """Every name made only of ASCII letters, digits, '-' and '_' is valid."""
    assert valid_name(name) is True


@given(st.text(min_size=0, max_size=8).filter(lambda s: not s or not set(s) <= set(_SAFE_CHARS)))
@example("")
@example("etl\n")
@example("etl\r\n")
@example("\netl")
@example(".")
@example("..")
@example("a.b")
@example("../x")
@example("a/b")
@example("a\\b")
@example("C:x")
@example("a b")
@example("relatório")
@example("ｅｔｌ")
@example("a\x00")
def test_a_name_with_any_other_character_is_invalid(name):
    """A name goes into paths and OS scheduler commands: empty, or one character outside
    [A-Za-z0-9_-] (a trailing newline included, df-utq8), makes it invalid."""
    assert valid_name(name) is False


@pytest.mark.parametrize("name", [None, 1, b"etl", ["etl"], Path("etl")])
def test_a_name_that_is_not_a_string_is_invalid(name):
    """Names come from YAML and HTTP: anything but a str is refused, never an exception."""
    assert valid_name(name) is False


# --- data_dir -------------------------------------------------------------------------------------

def test_an_empty_dataforge_home_is_the_same_as_unset(tmp_path, monkeypatch):
    """DATAFORGE_HOME='' does not make the current directory the home: the platform default is used."""
    monkeypatch.setenv("DATAFORGE_HOME", "")
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *a, **kw: str(tmp_path / "pd"))
    assert data_dir() == tmp_path / "pd"


def test_data_dir_creates_a_home_whose_parents_do_not_exist(tmp_path, monkeypatch, capsys):
    """A DATAFORGE_HOME several levels below an existing folder is created with its parents,
    and the notice says once where it was created."""
    home = tmp_path / "a" / "b" / "home"
    monkeypatch.setenv("DATAFORGE_HOME", str(home))
    assert data_dir() == home
    assert home.is_dir()
    assert data_dir() == home
    assert capsys.readouterr().out == f"Created DataForge data directory: {home}\n"


def _windows_user_data_dir(appname=None, appauthor=None, **kwargs):
    """platformdirs' Windows rule, run on any OS (the folder probe is patched by the caller)."""
    from platformdirs.windows import Windows
    return Windows(appname=appname, appauthor=appauthor, **kwargs).user_data_dir


def test_the_default_home_on_windows_is_local_app_data_dataforge(tmp_path, monkeypatch):
    """Docstring: Windows default is C:\\Users\\<user>\\AppData\\Local\\DataForge, one DataForge
    folder, not DataForge\\DataForge (platformdirs adds an author folder unless appauthor=False)."""
    monkeypatch.delenv("DATAFORGE_HOME", raising=False)
    local = tmp_path / "Local"
    monkeypatch.setattr("platformdirs.windows.get_win_folder", lambda csidl: str(local))
    monkeypatch.setattr(platformdirs, "user_data_dir", _windows_user_data_dir)
    assert data_dir() == local / "DataForge"


# --- db_url ---------------------------------------------------------------------------------------

# Characters a real profile or folder name can hold: space, accents, URL metacharacters. '%' and '?'
# have their own tests below (df-ahua.23.4); ':' cannot be in a Windows folder name.
_SEGMENT_CHARS = "abc XYZ09_-.éçãü#@&+=,;'()[]!$~" + (":" if sys.platform != "win32" else "")
_SEGMENT = st.text(alphabet=_SEGMENT_CHARS, min_size=1, max_size=8).filter(
    lambda s: s.strip(" .") == s and s not in (".", "..") and s.upper() not in _RESERVED)


def _opens(path: Path) -> None:
    """Assert that the URL db_url gives for *path* names that file and that SQLAlchemy creates it there."""
    url = db_url(path)
    assert "\\" not in url
    assert make_url(url).database == path.resolve().as_posix()
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("CREATE TABLE t (x INTEGER)")
            conn.commit()
    finally:
        engine.dispose()
    assert path.is_file()


@given(st.lists(_SEGMENT, min_size=1, max_size=3))
@example(["with space"])
@example(["relatório"])
@example(["a#b"])
@example(["a@b"])
@example(["a+b"])
def test_db_url_opens_the_file_at_that_path(segments):
    """The URL db_url gives, handed to SQLAlchemy, opens exactly that file: whatever the folder names
    hold, the database never lands elsewhere."""
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d).joinpath(*segments)
        folder.mkdir(parents=True)
        _opens(folder / "x.db")


def test_a_percent_sign_not_followed_by_hex_is_kept(tmp_path):
    """A folder named '100%' is not an escape sequence and is opened as is."""
    (tmp_path / "100%").mkdir()
    _opens(tmp_path / "100%" / "x.db")


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-ahua.23.4: SQLAlchemy decodes '%20' in the URL db_url builds")
def test_a_percent_escape_in_a_folder_name_is_not_decoded(tmp_path):
    """A folder literally named 'a%20b' holds the database, not a folder 'a b' next to it."""
    (tmp_path / "a%20b").mkdir()
    _opens(tmp_path / "a%20b" / "x.db")


@pytest.mark.skipif(sys.platform == "win32", reason="NTFS cannot hold '?' in a folder name")
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-ahua.23.4: '?' in the path starts the URL query string")
def test_a_question_mark_in_a_folder_name_is_part_of_the_path(tmp_path):
    """A folder named 'q?x=1' holds the database; the '?' does not cut the path short."""
    (tmp_path / "q?x=1").mkdir()
    _opens(tmp_path / "q?x=1" / "x.db")


def test_db_url_of_a_relative_path_is_absolute(tmp_path, monkeypatch):
    """A relative path is resolved against the current directory once, so the URL keeps pointing at
    the same file after a chdir."""
    monkeypatch.chdir(tmp_path)
    url = db_url(Path("x.db"))
    assert make_url(url).database == (tmp_path / "x.db").resolve().as_posix()


# --- resolve_db / global_db_url -------------------------------------------------------------------

def test_resolve_db_default_is_a_usable_database_in_a_fresh_workspace(tmp_path):
    """Without --db, the run history lives at <workspace>/data/dataforge.db, and init_db can create it
    even when the workspace has no data/ folder yet."""
    ws = tmp_path / "ws"
    ws.mkdir()
    engine = init_db(resolve_db(None, ws))
    engine.dispose()
    assert (ws / "data" / "dataforge.db").is_file()


def test_resolve_db_default_is_the_same_on_every_run(tmp_path):
    """Every run of a workspace resolves the same database, also once data/ already exists."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "dataforge.db").write_bytes(b"")
    first = resolve_db(None, tmp_path)
    assert resolve_db(None, tmp_path) == first == db_url(tmp_path / "data" / "dataforge.db")


@pytest.mark.parametrize("db", [None, ""])
def test_resolve_db_without_a_url_uses_the_workspace_database(tmp_path, db):
    """An empty --db is not a URL: it means the workspace default, like no --db at all."""
    assert resolve_db(db, tmp_path) == db_url(tmp_path / "data" / "dataforge.db")


def test_resolve_db_with_a_url_does_not_touch_the_workspace(tmp_path):
    """An explicit URL is used as given and creates nothing in the workspace."""
    assert resolve_db("sqlite:///elsewhere.db", tmp_path) == "sqlite:///elsewhere.db"
    assert list(tmp_path.iterdir()) == []


def test_global_db_url_opens_dataforge_db_in_the_home(tmp_path, monkeypatch):
    """The global run history is DATAFORGE_HOME/dataforge.db."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    engine = init_db(global_db_url())
    engine.dispose()
    assert (tmp_path / "dataforge.db").is_file()


# --- resolve_workspace / list_workspaces ----------------------------------------------------------

def _plant(home: Path, names: list[str], with_yaml: list[bool]) -> None:
    for name, has_yaml in zip(names, with_yaml, strict=True):
        ws = home / "workspaces" / name
        ws.mkdir(parents=True)
        if has_yaml:
            (ws / "workspace.yaml").write_bytes(f"name: {name}\n".encode("utf-8"))


_PLANTS = st.lists(_DIR_NAME, max_size=5, unique_by=str.lower).flatmap(
    lambda names: st.tuples(st.just(names), st.lists(st.booleans(), min_size=len(names), max_size=len(names))))


@given(_PLANTS)
@example((["b", "a", "c"], [True, True, False]))
def test_list_workspaces_is_the_sorted_folders_holding_workspace_yaml(plants):
    """A workspace is a folder under workspaces/ with a workspace.yaml; the list is sorted and every
    listed workspace resolves by its name to the same folder."""
    names, with_yaml = plants
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        _plant(home, names, with_yaml)
        (home / "workspaces").mkdir(exist_ok=True)
        (home / "workspaces" / "stray.yaml").write_bytes(b"name: stray\n")
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATAFORGE_HOME", str(home))
            listed = list_workspaces()
            expected = sorted(home / "workspaces" / n for n, y in zip(names, with_yaml, strict=True) if y)
            assert listed == expected
            for ws in listed:
                assert resolve_workspace(ws.name) == ws
            for n, y in zip(names, with_yaml, strict=True):
                if not y:
                    with pytest.raises(ValueError, match=f"Workspace '{n}' not found"):
                        resolve_workspace(n)


def test_list_workspaces_is_empty_without_a_workspaces_folder(tmp_path, monkeypatch):
    """A home without workspaces/ (a wrong DATAFORGE_HOME, df-pr6) lists nothing instead of failing."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    assert list_workspaces() == []


def test_list_workspaces_does_not_look_deeper_than_one_level(tmp_path, monkeypatch):
    """Only workspaces/<name>/workspace.yaml counts, not a workspace.yaml further down."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    nested = tmp_path / "workspaces" / "group" / "inner"
    nested.mkdir(parents=True)
    (nested / "workspace.yaml").write_bytes(b"name: inner\n")
    assert list_workspaces() == []


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-ahua.23.6: list_workspaces lists folders whose name resolve_workspace refuses")
def test_every_listed_workspace_resolves_by_its_name(tmp_path, monkeypatch):
    """The GUI and API list workspaces and then address them by name: a folder 'meu ws' holding a
    workspace.yaml must not be listed when resolve_workspace refuses that name."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    _plant(tmp_path, ["meu ws", "ok"], [True, True])
    assert [ws.name for ws in list_workspaces()] == ["ok"]


@pytest.mark.parametrize("name", ["", "etl\n", ".", "..", "a\\b", "C:x", "a b", "relatório"])
def test_resolve_workspace_refuses_an_unsafe_name_before_touching_the_disk(tmp_path, monkeypatch, name):
    """An unsafe workspace name is refused as invalid, never looked up as a path (df-utq8)."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="^Invalid workspace name"):
        resolve_workspace(name)


def test_resolve_workspace_needs_workspace_yaml_not_just_the_folder(tmp_path, monkeypatch):
    """A folder under workspaces/ without workspace.yaml is not a workspace; the error says which
    folder was searched, so a wrong DATAFORGE_HOME shows."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    (tmp_path / "workspaces" / "half").mkdir(parents=True)
    with pytest.raises(ValueError, match="^Workspace 'half' not found") as exc:
        resolve_workspace("half")
    assert str(tmp_path / "workspaces") in str(exc.value)


def test_resolve_workspace_without_a_name_is_the_current_workspace(tmp_path, monkeypatch):
    """Without --workspace, the current directory is the workspace when it holds workspace.yaml."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path / "home"))
    ws = tmp_path / "here"
    ws.mkdir()
    (ws / "workspace.yaml").write_bytes(b"name: here\n")
    monkeypatch.chdir(ws)
    assert resolve_workspace(None) == ws


def test_resolve_workspace_without_a_name_outside_a_workspace_fails(tmp_path, monkeypatch):
    """Without --workspace and outside a workspace, the error says to pass --workspace."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="--workspace"):
        resolve_workspace(None)


# --- allowed_hosts --------------------------------------------------------------------------------

_HOST = st.text(alphabet="abcXYZ09.-", min_size=1, max_size=10)
_PADDED_HOST = st.tuples(st.sampled_from(["", " ", "\t"]), _HOST, st.sampled_from(["", " ", " \t"])).map("".join)


@given(st.lists(st.one_of(_PADDED_HOST, st.just(""), st.just(" ")), max_size=5))
@example([" Example.COM ", "", "api.local"])
@example([])
def test_allowed_hosts_is_loopback_plus_the_listed_names(entries):
    """Loopback is always allowed; each comma-separated name is added trimmed and lower-cased, and
    empty entries add nothing."""
    expected = {"127.0.0.1", "localhost", "::1"} | {e.strip().lower() for e in entries if e.strip()}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(compat.ALLOWED_HOSTS_ENV, ",".join(entries))
        assert allowed_hosts() == expected


@pytest.mark.parametrize("value", ["*", " * ", "a.com,*", "*,a.com"])
def test_a_star_anywhere_turns_the_host_check_off(monkeypatch, value):
    """'*' in DATAFORGE_ALLOWED_HOSTS means no Host check at all (None)."""
    monkeypatch.setenv(compat.ALLOWED_HOSTS_ENV, value)
    assert allowed_hosts() is None


def test_allowed_hosts_unset_is_loopback_only(monkeypatch):
    """Without the variable the API answers to loopback names only."""
    monkeypatch.delenv(compat.ALLOWED_HOSTS_ENV, raising=False)
    assert allowed_hosts() == {"127.0.0.1", "localhost", "::1"}


# --- api_port -------------------------------------------------------------------------------------

@given(st.integers(1, 65535))
@example(1)
@example(65535)
def test_any_port_number_is_read_back(port):
    """Every value from 1 to 65535 is a port, also with the trailing space `set X=8123 ` leaves in cmd."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(compat.PORT_ENV, str(port))
        assert api_port() == port
        mp.setenv(compat.PORT_ENV, f" {port} ")
        assert api_port() == port


@given(st.one_of(st.integers(max_value=0), st.integers(min_value=65536)))
@example(0)
@example(65536)
def test_a_number_outside_the_port_range_names_the_variable(port):
    """0, negatives and anything above 65535 fail with the message naming DATAFORGE_PORT (df-yvgx)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(compat.PORT_ENV, str(port))
        with pytest.raises(ValueError, match=r"^DATAFORGE_PORT must be a port number \(1-65535\)"):
            api_port()


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-ahua.23.5: '²'.isdigit() is True, so int() raises its own message")
def test_a_unicode_digit_that_is_not_a_number_names_the_variable(monkeypatch):
    """DATAFORGE_PORT='²' is not a port: the error names the variable like every other bad value."""
    monkeypatch.setenv(compat.PORT_ENV, "²")
    with pytest.raises(ValueError, match="^DATAFORGE_PORT must be a port number"):
        api_port()


def test_the_default_port_is_8000(monkeypatch):
    """Without DATAFORGE_PORT the API listens on 8000."""
    monkeypatch.delenv(compat.PORT_ENV, raising=False)
    assert api_port() == DEFAULT_API_PORT == 8000


# --- workspace layout -----------------------------------------------------------------------------

def test_apps_and_curated_folders_follow_the_workspace_layout(tmp_path):
    """docs/workspace-layout.md: apps live in <workspace>/apps, curated tables in <workspace>/data/curated;
    asking for the path creates nothing."""
    assert apps_dir(tmp_path) == tmp_path / "apps"
    assert curated_dir(tmp_path) == tmp_path / "data" / "curated"
    assert list(tmp_path.iterdir()) == []
