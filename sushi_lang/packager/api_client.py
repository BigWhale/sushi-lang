"""HTTP client for Omakase API requests."""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status}: {message}")


def api_request(
    repository: str,
    path: str,
    token: str | None = None,
    method: str = "GET",
) -> dict:
    """Make an authenticated JSON request to the Omakase API.

    Args:
        repository: Repository hostname (e.g. "omakase.lubica.net").
        path: API path (e.g. "/users/me").
        token: Bearer token (optional).
        method: HTTP method.

    Returns:
        Parsed JSON response dict.

    Raises:
        ApiError: On HTTP error responses.
        ConnectionError: On network failures.
    """
    url = f"https://{repository}/api/v1{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "nori/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = json.loads(e.read().decode()).get("detail", "")
        except Exception:
            pass
        raise ApiError(e.code, body or f"HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ConnectionError(f"Could not connect to {repository}: {reason}")


def api_upload_multipart(
    repository: str,
    path: str,
    token: str,
    parts: list[tuple[str, str, str, bytes]],
    extra_headers: dict[str, str] | None = None,
) -> dict:
    """Upload multipart/form-data to the Omakase API.

    Args:
        repository: Repository hostname.
        path: API path.
        token: Bearer token (required).
        parts: List of (field_name, filename, content_type, data) tuples.
        extra_headers: Additional headers (e.g. X-Sha256).

    Returns:
        Parsed JSON response dict.
    """
    boundary = f"----NoriBoundary{os.urandom(16).hex()}"
    body = b""
    for field_name, filename, content_type, data in parts:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
        body += f"Content-Type: {content_type}\r\n\r\n".encode()
        body += data
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    url = f"https://{repository}/api/v1{path}"
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
        "User-Agent": "nori/1.0",
        "Authorization": f"Bearer {token}",
    }
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode()).get("detail", "")
        except Exception:
            pass
        raise ApiError(e.code, detail or f"HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ConnectionError(f"Could not connect to {repository}: {reason}")
