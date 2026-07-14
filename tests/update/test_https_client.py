from __future__ import annotations

import http.client
import io
import ssl
from unittest.mock import patch

import pytest

from unified_can_lin_host_tool.update.errors import UpdateNetworkError
from unified_can_lin_host_tool.update.https_client import SafeHttpsClient, validate_github_https_url


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b/releases/x",
        "https://github.com:444/a/b/releases/x",
        "https://user@github.com/a/b/releases/x",
        "https://github.com.evil.example/a/b",
        "https://evilgithubusercontent.com/a/b",
    ],
)
def test_github_url_gate_rejects_unsafe_targets(url):
    with pytest.raises(UpdateNetworkError):
        validate_github_https_url(url)


def test_github_url_gate_accepts_release_asset_hosts():
    validate_github_https_url("https://github.com/a/b/releases/download/v1.2.3/update.json")
    validate_github_https_url("https://objects.githubusercontent.com/path")


class _Response:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = io.BytesIO(body)
        self._headers = headers or {}

    def getheader(self, name):
        return self._headers.get(name)

    def read(self, size=-1):
        return self._body.read(size)

    def close(self):
        pass


class _Connection:
    queued_responses = []
    requests = []
    timeouts = []

    def __init__(self, host, port, *, timeout, context):
        self.host = host
        self.port = port
        self.context = context
        self.sock = None
        self.timeouts.append(timeout)

    def request(self, method, target, headers):
        self.requests.append((self.host, method, target, headers))

    def getresponse(self):
        return self.queued_responses.pop(0)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def reset_fake_connection():
    _Connection.queued_responses = []
    _Connection.requests = []
    _Connection.timeouts = []


def test_read_bytes_revalidates_each_redirect_and_sends_no_cache_headers():
    _Connection.queued_responses = [
        _Response(302, headers={"Location": "https://objects.githubusercontent.com/release/update.json"}),
        _Response(200, b"{}", {"Content-Length": "2"}),
    ]

    with patch("http.client.HTTPSConnection", _Connection):
        result = SafeHttpsClient().read_bytes(
            "https://github.com/o/r/releases/latest/download/update.json",
            max_bytes=64,
            no_cache=True,
        )

    assert result == b"{}"
    assert [request[0] for request in _Connection.requests] == ["github.com", "objects.githubusercontent.com"]
    assert all(request[3]["Cache-Control"] == "no-cache" for request in _Connection.requests)
    assert all(request[3]["Pragma"] == "no-cache" for request in _Connection.requests)


def test_redirect_to_disallowed_host_is_rejected_before_second_request():
    _Connection.queued_responses = [_Response(302, headers={"Location": "https://attacker.example/update.json"})]

    with patch("http.client.HTTPSConnection", _Connection), pytest.raises(UpdateNetworkError):
        SafeHttpsClient().read_bytes("https://github.com/o/r/releases/latest", max_bytes=64)

    assert len(_Connection.requests) == 1


def test_more_than_five_redirects_are_rejected():
    _Connection.queued_responses = [
        _Response(302, headers={"Location": f"https://github.com/o/r/{index}"}) for index in range(6)
    ]

    with patch("http.client.HTTPSConnection", _Connection), pytest.raises(UpdateNetworkError, match="重定向"):
        SafeHttpsClient().read_bytes("https://github.com/o/r/start", max_bytes=64)


def test_content_length_over_limit_is_rejected_without_reading_body():
    response = _Response(200, b"not-read", {"Content-Length": "65"})
    _Connection.queued_responses = [response]

    with patch("http.client.HTTPSConnection", _Connection), pytest.raises(UpdateNetworkError, match="大小"):
        SafeHttpsClient().read_bytes("https://github.com/o/r/update.json", max_bytes=64)

    assert response._body.tell() == 0


def test_unknown_content_length_is_bounded_while_streaming():
    _Connection.queued_responses = [_Response(200, b"12345")]

    with patch("http.client.HTTPSConnection", _Connection), pytest.raises(UpdateNetworkError, match="大小"):
        list(SafeHttpsClient().iter_bytes("https://github.com/o/r/asset.exe", max_bytes=4))


def test_transport_exception_maps_to_stable_network_error_without_url_details():
    class FailingConnection(_Connection):
        def request(self, method, target, headers):
            raise TimeoutError("secret.example/path")

    with patch("http.client.HTTPSConnection", FailingConnection), pytest.raises(UpdateNetworkError) as exc_info:
        SafeHttpsClient().read_bytes("https://github.com/o/r/update.json", max_bytes=64)

    assert exc_info.value.code == "UPDATE_NETWORK_UNAVAILABLE"
    assert "secret.example" not in str(exc_info.value)


def test_read_timeout_is_applied_before_response_headers_are_read():
    class Socket:
        timeout = None

        def settimeout(self, timeout):
            self.timeout = timeout

    class HeaderDelayConnection(_Connection):
        socket = Socket()

        def request(self, method, target, headers):
            super().request(method, target, headers)
            self.sock = self.socket

        def getresponse(self):
            assert self.sock.timeout == 7.0
            return _Response(200, b"ok", {"Content-Length": "2"})

    with patch("http.client.HTTPSConnection", HeaderDelayConnection):
        result = SafeHttpsClient().read_bytes(
            "https://github.com/o/r/update.json",
            max_bytes=2,
            read_timeout_s=7.0,
        )

    assert result == b"ok"


@pytest.mark.parametrize("failure", [ssl.SSLError("TLS failed"), OSError("trust store failed")])
def test_tls_context_creation_failure_maps_to_stable_network_error(failure):
    with patch("ssl.create_default_context", side_effect=failure), pytest.raises(UpdateNetworkError) as exc_info:
        SafeHttpsClient().read_bytes("https://github.com/o/r/update.json", max_bytes=64)

    assert exc_info.value.code == "UPDATE_NETWORK_UNAVAILABLE"


@pytest.mark.parametrize(
    "failure",
    [http.client.HTTPException("connection failed"), ValueError("invalid connection")],
)
def test_https_connection_construction_failure_maps_to_stable_network_error(failure):
    with patch("http.client.HTTPSConnection", side_effect=failure), pytest.raises(UpdateNetworkError) as exc_info:
        SafeHttpsClient().read_bytes("https://github.com/o/r/update.json", max_bytes=64)

    assert exc_info.value.code == "UPDATE_NETWORK_UNAVAILABLE"
