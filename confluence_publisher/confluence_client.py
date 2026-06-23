from __future__ import annotations

import atexit
import base64
import logging
import os
import tempfile

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class RetryableError(requests.RequestException):
    pass


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RetryableError, requests.ConnectionError, requests.Timeout))


class ConfluenceClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        mode: str,
        email: str | None = None,
        cert_pem_b64: str | None = None,
        max_requests: int = 3,
    ):
        if mode not in ("dc", "cloud"):
            raise ValueError(f"mode must be 'dc' or 'cloud', got '{mode}'")
        self.mode = mode
        self.base_url = base_url.rstrip("/")
        pem_path = _write_pem(cert_pem_b64) if cert_pem_b64 else None
        self._session = self._build_session(token, email, pem_path)

    def _build_session(self, token: str, email: str | None, pem_path: str | None) -> requests.Session:
        session = requests.Session()
        if self.mode == "dc":
            session.headers["Authorization"] = f"Bearer {token}"
            if pem_path:
                session.cert = pem_path
        else:
            encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
            session.headers["Authorization"] = f"Basic {encoded}"
        session.headers["Content-Type"] = "application/json"
        return session

    def _url(self, path: str) -> str:
        if self.mode == "dc":
            return f"{self.base_url}/rest/api/content/{path.lstrip('/')}"
        return f"{self.base_url}/wiki/api/v2/{path.lstrip('/')}"

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=300),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((RetryableError, requests.ConnectionError, requests.Timeout)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        response = self._session.request(method, url, timeout=30, **kwargs)
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableError(
                f"HTTP {response.status_code} from {url} - will retry",
                response=response,
            )
        response.raise_for_status()
        return response

    def get_page(self, page_id: str) -> dict:
        if self.mode == "dc":
            url = self._url(f"{page_id}?expand=version,body.storage")
            data = self._request("GET", url).json()
            return {
                "version": data["version"]["number"],
                "body": data["body"]["storage"]["value"],
            }
        else:
            url = self._url(f"pages/{page_id}?body-format=storage")
            data = self._request("GET", url).json()
            # Cloud v2 returns body.storage.value when body-format=storage is requested
            body_val = (
                data.get("body", {}).get("storage", {}).get("value", "")
            )
            return {
                "version": data["version"]["number"],
                "body": body_val,
            }

    def update_page(
        self,
        page_id: str,
        title: str,
        body: str,
        version: int,
        commit_sha: str = "",
    ) -> dict:
        if self.mode == "dc":
            url = self._url(str(page_id))
            payload = {
                "version": {"number": version, "message": commit_sha},
                "title": title,
                "type": "page",
                "body": {"storage": {"value": body, "representation": "storage"}},
            }
        else:
            url = self._url(f"pages/{page_id}")
            payload = {
                "id": page_id,
                "status": "current",
                "version": {"number": version, "message": commit_sha},
                "title": title,
                "body": {"representation": "storage", "value": body},
            }
        return self._request("PUT", url, json=payload).json()

    def page_exists(self, page_id: str) -> bool:
        try:
            self.get_page(page_id)
            return True
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return False
            raise


def _write_pem(encoded: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        os.write(fd, base64.b64decode(encoded))
    finally:
        os.close(fd)
    atexit.register(os.unlink, path)
    return path
