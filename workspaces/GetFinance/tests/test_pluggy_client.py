"""functions.py Pluggy client: every call has a timeout, and the API key is cached for its TTL.

A request with no timeout can hang the daily job forever on a stalled connection (ruff S113,
found when the quality gate started seeing this workspace, DataForge df-grvi).
"""
import datetime
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from tests._delta_util import SCRIPTS

FUNCTIONS_PATH = SCRIPTS / "functions" / "functions.py"


@pytest.fixture
def fn(monkeypatch):
    monkeypatch.setenv("CLIENT_ID", "test-client")
    monkeypatch.setenv("CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("ITEM_IDS", "item-111")
    spec = importlib.util.spec_from_file_location("functions_pluggy", FUNCTIONS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ok(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    r.text = ""
    return r


def test_every_pluggy_call_has_a_timeout(fn):
    page = {"results": [{"id": "x"}], "total": 1, "next": None}
    with patch("requests.post", return_value=_ok({"apiKey": "k"})) as post, \
         patch("requests.get", return_value=_ok(page)) as get:
        fn.list_accounts("item")
        fn.list_transactions("acc")
        fn.list_bills("acc")
        fn.list_investments("item")
    calls = post.call_args_list + get.call_args_list
    assert len(calls) == 5   # 1 token + 4 endpoints (one page each)
    assert {c.kwargs.get("timeout") for c in calls} == {fn.PLUGGY_TIMEOUT_SECONDS}
    assert fn.PLUGGY_TIMEOUT_SECONDS > 0


def test_api_key_is_reused_within_its_ttl_and_renewed_after(fn, monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr(fn.time, "time", lambda: clock["now"])
    with patch("requests.post", side_effect=[_ok({"apiKey": "first"}), _ok({"apiKey": "second"})]) as post:
        assert fn.get_api_token() == "first"
        clock["now"] += fn.PLUGGY_TOKEN_TTL_SECONDS - 1
        assert fn.get_api_token() == "first"
        clock["now"] += 1
        assert fn.get_api_token() == "second"
    assert post.call_count == 2


def test_a_failed_auth_raises_and_caches_nothing(fn):
    # df-ahua.10.1: a refused auth raises (it used to return None and let the run end with success).
    bad = MagicMock(status_code=401, text="unauthorized")
    with patch("requests.post", return_value=bad) as post:
        for _ in range(2):
            with pytest.raises(RuntimeError, match=r"^Pluggy POST /auth answered HTTP 401$"):
                fn.get_api_token()
    assert post.call_count == 2
    assert fn._api_token_cache["token"] is None


def test_a_200_auth_without_an_api_key_fails_the_first_call(fn):
    with patch("requests.post", return_value=_ok({})), patch("requests.get") as get, \
         pytest.raises(RuntimeError, match="200 without an apiKey"):
        fn.list_accounts("item")
    assert get.call_count == 0


# --- df-ahua.10: the same rules as tests/test_landing2raw_fake_pluggy.py, on the module loaded from its
# real path (that file runs a copy of the script, which coverage cannot see) -------------------------

def _pages(*bodies):
    return [_ok(b) for b in bodies]


@pytest.mark.parametrize("name", ["list_accounts", "list_transactions", "list_bills", "list_investments"])
def test_no_api_key_raises_before_any_get(fn, name):
    with patch.object(fn, "get_api_token", return_value=None), patch("requests.get") as get, \
         pytest.raises(RuntimeError, match="200 without an apiKey"):
        getattr(fn, name)("key")
    assert get.call_count == 0


def test_a_repeated_cursor_raises_before_asking_again(fn):
    pages = _pages({"results": [{"id": "t1"}], "next": "accountId=a&cursor=X"},
                   {"results": [{"id": "t2"}], "next": "accountId=a&cursor=X"})
    with patch.object(fn, "get_api_token", return_value="k"), patch("requests.get", side_effect=pages) as get, \
         pytest.raises(RuntimeError, match="next cursor repeats at page 3"):
        fn.list_transactions("a")
    assert get.call_count == 2


def test_the_cursor_page_cap_raises(fn, monkeypatch):
    monkeypatch.setattr(fn, "PLUGGY_MAX_PAGES", 2)
    pages = _pages(*({"results": [], "next": f"accountId=a&cursor={n}"} for n in range(5)))
    with patch.object(fn, "get_api_token", return_value="k"), patch("requests.get", side_effect=pages) as get, \
         pytest.raises(RuntimeError, match="still a next cursor after 2 pages"):
        fn.list_transactions("a")
    assert get.call_count == 2


@pytest.mark.parametrize("nxt", ["https://elsewhere.invalid/v2/transactions?cursor=1",
                                 "http://api.pluggy.ai/v2/transactions?cursor=1"])
def test_a_next_url_off_pluggy_raises_unsent(fn, nxt):
    pages = _pages({"results": [{"id": "t1"}], "next": nxt})
    with patch.object(fn, "get_api_token", return_value="k"), patch("requests.get", side_effect=pages) as get, \
         pytest.raises(RuntimeError, match="refusing a next URL"):
        fn.list_transactions("a")
    assert get.call_count == 1


@pytest.mark.parametrize("name", ["list_bills", "list_investments"])
def test_numbered_pages_stop_on_an_empty_page_with_a_warning(fn, name, capsys):
    pages = _pages({"results": [{"id": "x"}], "total": 9}, {"results": [], "total": 9})
    with patch.object(fn, "get_api_token", return_value="k"), patch("requests.get", side_effect=pages):
        got = getattr(fn, name)("key")
    assert got == {"results": [{"id": "x"}], "total": 1}
    assert "page 2 came back empty with 1 of total=9 rows read" in capsys.readouterr().out


@pytest.mark.parametrize("page", [{"results": [{"id": "x"}, {"id": "y"}], "total": 1},
                                  {"results": [{"id": "x"}], "total": 1, "totalPages": 3}])
def test_numbered_pages_warn_when_the_counts_disagree(fn, page, capsys):
    with patch.object(fn, "get_api_token", return_value="k"), patch("requests.get", side_effect=_pages(page)):
        got = fn.list_bills("key")
    assert got["results"] == page["results"]
    assert "Pluggy's counts disagree" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["list_bills", "list_investments"])
def test_the_numbered_page_cap_raises(fn, name, monkeypatch):
    monkeypatch.setattr(fn, "PLUGGY_MAX_PAGES", 2)
    pages = _pages(*({"results": [{"id": str(n)}], "total": 9} for n in range(5)))
    with patch.object(fn, "get_api_token", return_value="k"), patch("requests.get", side_effect=pages) as get, \
         pytest.raises(RuntimeError, match="2 of total=9 rows after 2 pages"):
        getattr(fn, name)("key")
    assert get.call_count == 2


@pytest.fixture
def raw_at(fn, monkeypatch, tmp_path):
    monkeypatch.setattr(fn, "_connection_config", lambda name: {"type": "parquet_dir", "path": "raw/"})
    monkeypatch.setattr(fn, "_connection_root", lambda name: tmp_path / "raw")
    return tmp_path / "raw" / "bills" / "ingestion_date=2030-01-15"


def test_a_partition_write_replaces_through_a_temp_file(fn, raw_at):
    """df-ahua.10.5: the write leaves data.parquet only; a leftover temp is overwritten; a rewrite replaces."""
    day = datetime.date(2030, 1, 15)
    raw_at.mkdir(parents=True)
    (raw_at / fn.RAW_PARTITION_TEMP).write_bytes(b"torn")
    fn.write_parquet_partition("raw", "bills", [{"id": "b1"}], day)
    out = fn.write_parquet_partition("raw", "bills", [{"id": "b2"}], day)
    assert out == raw_at / "data.parquet"
    assert sorted(p.name for p in raw_at.iterdir()) == ["data.parquet"]
    assert pl.read_parquet(out)["id"].to_list() == ["b2"]


def test_a_partition_write_to_a_connection_that_is_not_parquet_dir_raises(fn, monkeypatch, tmp_path):
    monkeypatch.setattr(fn, "_connection_config", lambda name: {"type": "delta_dir", "path": "cleansed/"})
    day = datetime.date(2030, 1, 15)
    with pytest.raises(ValueError, match="not parquet_dir"):
        fn.write_parquet_partition("cleansed", "bills", [{"id": "b1"}], day)
    assert not any(tmp_path.iterdir())


def test_a_failed_partition_write_keeps_the_earlier_file(fn, raw_at, monkeypatch):
    day = datetime.date(2030, 1, 15)
    out = fn.write_parquet_partition("raw", "bills", [{"id": "b1"}], day)
    before = out.read_bytes()

    def torn(self, file, *args, **kwargs):
        Path(file).write_bytes(b"PAR1 half")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", torn)
    with pytest.raises(OSError, match="No space"):
        fn.write_parquet_partition("raw", "bills", [{"id": "b2"}], day)
    assert out.read_bytes() == before
