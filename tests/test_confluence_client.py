from unittest.mock import MagicMock, patch

import pytest
import requests

from confluence_publisher.confluence_client import ConfluenceClient


def make_client(mode: str = "cloud") -> ConfluenceClient:
    return ConfluenceClient(
        base_url="https://example.atlassian.net",
        token="token",
        mode=mode,
        email="user@example.com",
    )


def mock_response(status: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


# --- create_page (Cloud) ---

def test_create_page_cloud_calls_v2(tmp_path):
    client = make_client("cloud")
    client._space_id_cache["MYSPACE"] = "123"

    with patch.object(client, "_request", return_value=mock_response(200, {"id": "456"})) as mock_req:
        page_id = client.create_page("My Page", "MYSPACE", "100", "<p>body</p>")

    assert page_id == "456"
    url = mock_req.call_args[0][1]
    assert "/wiki/api/v2/pages" in url
    payload = mock_req.call_args[1]["json"]
    assert payload["spaceId"] == "123"
    assert payload["parentId"] == "100"
    assert payload["title"] == "My Page"
    assert payload["body"]["value"] == "<p>body</p>"


def test_create_page_cloud_no_parent(tmp_path):
    client = make_client("cloud")
    client._space_id_cache["MYSPACE"] = "123"

    with patch.object(client, "_request", return_value=mock_response(200, {"id": "456"})) as mock_req:
        client.create_page("My Page", "MYSPACE", "", "<p>body</p>")

    payload = mock_req.call_args[1]["json"]
    assert "parentId" not in payload


def test_create_page_dc_calls_v1():
    client = make_client("dc")

    with patch.object(client, "_request", return_value=mock_response(200, {"id": "789"})) as mock_req:
        page_id = client.create_page("DC Page", "DCSPACE", "50", "<p>body</p>")

    assert page_id == "789"
    url = mock_req.call_args[0][1]
    assert "/rest/api/content" in url
    payload = mock_req.call_args[1]["json"]
    assert payload["space"]["key"] == "DCSPACE"
    assert payload["ancestors"] == [{"id": "50"}]


def test_create_page_dc_no_parent():
    client = make_client("dc")

    with patch.object(client, "_request", return_value=mock_response(200, {"id": "789"})) as mock_req:
        client.create_page("DC Page", "DCSPACE", "", "<p>body</p>")

    payload = mock_req.call_args[1]["json"]
    assert "ancestors" not in payload


# --- _resolve_space_id ---

def test_resolve_space_id_cached():
    client = make_client("cloud")
    client._space_id_cache["MYSPACE"] = "123"

    with patch.object(client, "_request") as mock_req:
        sid = client._resolve_space_id("MYSPACE")

    assert sid == "123"
    mock_req.assert_not_called()


def test_resolve_space_id_fetches_and_caches():
    client = make_client("cloud")

    with patch.object(client, "_request", return_value=mock_response(200, {
        "results": [{"id": "456", "key": "MYSPACE"}]
    })) as mock_req:
        sid = client._resolve_space_id("MYSPACE")

    assert sid == "456"
    assert client._space_id_cache["MYSPACE"] == "456"
    mock_req.assert_called_once()


def test_resolve_space_id_not_found():
    client = make_client("cloud")

    with patch.object(client, "_request", return_value=mock_response(200, {"results": []})):
        with pytest.raises(ValueError, match="not found"):
            client._resolve_space_id("BADSPACE")


# --- upload_attachment ---

def test_upload_attachment_cloud():
    client = make_client("cloud")

    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = mock_response(200, {})
        client.upload_attachment("123", "fig.png", b"\x89PNG", "image/png")

    mock_req.assert_called_once()
    url = mock_req.call_args[0][1]   # _request("POST", url, ...)
    assert "/wiki/api/v2/pages/123/attachments" in url
    kwargs = mock_req.call_args[1]
    headers = kwargs["headers"]
    assert "X-Atlassian-Token" in headers
    # Content-Type: None lets requests auto-set the correct multipart boundary
    assert headers.get("Content-Type") is None
    files = kwargs["files"]
    assert files["file"][0] == "fig.png"
    assert files["file"][1] == b"\x89PNG"


def test_upload_attachment_dc():
    client = make_client("dc")

    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = mock_response(200, {})
        client.upload_attachment("123", "fig.png", b"data", "image/png")

    url = mock_req.call_args[0][1]
    assert "/rest/api/content/123/child/attachment" in url


def test_upload_attachment_uses_request_for_retry():
    """Attachments must route through _request so the 429/5xx retry policy applies."""
    client = make_client("dc")

    with patch.object(client, "_request", return_value=mock_response(200, {})) as mock_req:
        with patch.object(client._session, "post") as mock_bare_post:
            client.upload_attachment("123", "fig.png", b"data", "image/png")

    mock_req.assert_called_once()
    mock_bare_post.assert_not_called()


def test_upload_attachment_raises_on_error():
    client = make_client("cloud")

    with patch.object(client, "_request") as mock_req:
        mock_req.side_effect = requests.HTTPError("400 Bad Request")
        with pytest.raises(requests.HTTPError):
            client.upload_attachment("123", "fig.png", b"data", "image/png")


# --- get_page ---

def test_get_page_cloud():
    client = make_client("cloud")
    resp_data = {
        "version": {"number": 3},
        "body": {"storage": {"value": "<p>content</p>"}},
    }
    with patch.object(client, "_request", return_value=mock_response(200, resp_data)):
        page = client.get_page("42")

    assert page["version"] == 3
    assert page["body"] == "<p>content</p>"


def test_get_page_dc():
    client = make_client("dc")
    resp_data = {
        "version": {"number": 2},
        "body": {"storage": {"value": "<p>dc content</p>"}},
    }
    with patch.object(client, "_request", return_value=mock_response(200, resp_data)):
        page = client.get_page("99")

    assert page["version"] == 2
    assert page["body"] == "<p>dc content</p>"


# --- page_exists ---

def test_page_exists_true():
    client = make_client("cloud")
    with patch.object(client, "get_page", return_value={"version": 1, "body": ""}):
        assert client.page_exists("42") is True


def test_page_exists_false_on_404():
    client = make_client("cloud")
    not_found = requests.HTTPError()
    not_found.response = mock_response(404)
    with patch.object(client, "get_page", side_effect=not_found):
        assert client.page_exists("999") is False


def test_page_exists_reraises_non_404():
    client = make_client("cloud")
    server_err = requests.HTTPError()
    server_err.response = mock_response(500)
    with patch.object(client, "get_page", side_effect=server_err):
        with pytest.raises(requests.HTTPError):
            client.page_exists("999")
