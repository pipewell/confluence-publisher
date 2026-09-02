from confluence_publisher.cli import _get_repo_url


def test_get_repo_url_none_outside_actions(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert _get_repo_url() is None


def test_get_repo_url_default_server(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "pipewell/confluence-publisher")
    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    assert _get_repo_url() == "https://github.com/pipewell/confluence-publisher"


def test_get_repo_url_respects_ghe_server_url(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example.com")
    assert _get_repo_url() == "https://github.example.com/org/repo"


def test_get_repo_url_strips_trailing_slash_on_server_url(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example.com/")
    assert _get_repo_url() == "https://github.example.com/org/repo"
