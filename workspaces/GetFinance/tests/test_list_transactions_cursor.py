"""Tests for list_transactions cursor-based pagination (v2 API)."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


FUNCTIONS_PATH = Path(__file__).resolve().parent.parent / "scripts" / "functions" / "functions.py"


def _load_functions(monkeypatch):
    """Load functions.py into a fresh module namespace.

    Mirrors the exec() approach in test_landing2raw.py but uses importlib so
    the returned module has proper attribute access.
    """
    # Ensure env vars are set so module-level os.getenv calls don't return None
    monkeypatch.setenv("CLIENT_ID", "test-client")
    monkeypatch.setenv("CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("ITEM_IDS", "item-111")

    spec = importlib.util.spec_from_file_location("functions_dev", FUNCTIONS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_response(status_code, json_data):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data
    r.text = str(json_data)
    return r


# ---------------------------------------------------------------------------
# (a) multi-page cursor
# ---------------------------------------------------------------------------
def test_multi_page_cursor(monkeypatch):
    """Two pages via bare query-string cursor; stops when next=None."""
    mod = _load_functions(monkeypatch)

    page1 = _make_response(200, {
        "results": [{"id": "t1"}],
        "next": "accountId=acc-1&pageSize=500&cursor=ABC",
    })
    page2 = _make_response(200, {
        "results": [{"id": "t2"}],
        "next": None,
    })

    with patch.object(mod, "get_api_token", return_value="fake-token"), \
         patch("requests.get", side_effect=[page1, page2]) as mock_get:
        result = mod.list_transactions("acc-1")

    assert result is not None
    assert result["total"] == 2
    ids = [r["id"] for r in result["results"]]
    assert ids == ["t1", "t2"]

    assert mock_get.call_count == 2
    second_url = mock_get.call_args_list[1][0][0]
    assert "cursor=ABC" in second_url
    # traversal stopped (no third call)
    assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# (b) single page — next=None immediately
# ---------------------------------------------------------------------------
def test_single_page(monkeypatch):
    """Single page with next=None returns 1 result and calls requests.get once."""
    mod = _load_functions(monkeypatch)

    page1 = _make_response(200, {"results": [{"id": "t1"}], "next": None})

    with patch.object(mod, "get_api_token", return_value="fake-token"), \
         patch("requests.get", return_value=page1) as mock_get:
        result = mod.list_transactions("acc-1")

    assert result is not None
    assert result["total"] == 1
    assert result["results"][0]["id"] == "t1"
    assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# (c) full-URL next
# ---------------------------------------------------------------------------
def test_full_url_next(monkeypatch):
    """When next is a full URL, the second call uses that exact URL."""
    mod = _load_functions(monkeypatch)

    full_next = "https://api.pluggy.ai/v2/transactions?cursor=XYZ"
    page1 = _make_response(200, {"results": [{"id": "t1"}], "next": full_next})
    page2 = _make_response(200, {"results": [{"id": "t2"}], "next": None})

    with patch.object(mod, "get_api_token", return_value="fake-token"), \
         patch("requests.get", side_effect=[page1, page2]) as mock_get:
        result = mod.list_transactions("acc-1")

    assert result is not None
    assert result["total"] == 2
    second_url = mock_get.call_args_list[1][0][0]
    assert second_url == full_next


# ---------------------------------------------------------------------------
# (d) error — non-200 response raises (df-ahua.10.1)
# ---------------------------------------------------------------------------
def test_error_raises(monkeypatch):
    """A 500 response makes list_transactions raise, naming the route and status only."""
    mod = _load_functions(monkeypatch)

    err = _make_response(500, {"error": "internal"})

    with patch.object(mod, "get_api_token", return_value="fake-token"), \
         patch("requests.get", return_value=err), \
         pytest.raises(RuntimeError, match=r"^Pluggy GET /v2/transactions \(page 1\) answered HTTP 500$"):
        mod.list_transactions("acc-1")
