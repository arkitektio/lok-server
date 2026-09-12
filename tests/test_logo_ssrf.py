"""SSRF guards on the unauthenticated logo fetch.

`download_logo` is reached from `/f/start/`, `/f/servicestart/` and `/f/hubstart/`
with a URL taken straight from an unauthenticated request body, so it could be
used to make lok issue requests into its own docker network (minio, postgres,
ionscale) or to hang a worker forever on a tarpit.

These tests assert the request is refused *before* any socket is opened, so a
failure here is a real regression and not just a slow test.
"""

import pytest

from fakts.utils import _assert_public_url, download_logo


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/logo.png",  # plaintext
        "file:///etc/passwd",           # non-http scheme
        "ftp://example.com/logo.png",
        "gopher://example.com/",
        "https://127.0.0.1/logo.png",   # loopback
        "https://localhost/logo.png",
        "https://10.0.0.5/logo.png",    # RFC1918
        "https://192.168.1.1/logo.png",
        "https://172.16.0.1/logo.png",
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata
        "https://[::1]/logo.png",       # v6 loopback
        "https://0.0.0.0/logo.png",
    ],
)
def test_non_public_urls_are_refused(url):
    with pytest.raises(ValueError):
        _assert_public_url(url)


def test_internal_service_names_are_refused(monkeypatch):
    """The real attack shape: a docker-network service name, not a literal IP."""
    import socket

    def _fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("172.18.0.4", port))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    with pytest.raises(ValueError, match="non-public address"):
        _assert_public_url("https://minio/logo.png")


def test_host_resolving_to_both_public_and_private_is_refused(monkeypatch):
    """DNS returning a mix must not pass on the strength of the public answer."""
    import socket

    def _fake_getaddrinfo(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    with pytest.raises(ValueError, match="non-public address"):
        _assert_public_url("https://sneaky.example.com/logo.png")


def test_download_refuses_internal_url_without_opening_a_socket(monkeypatch):
    """The guard must run before `requests.get`, not alongside it."""
    import requests

    def _explode(*args, **kwargs):
        raise AssertionError("a request was issued for a blocked URL")

    monkeypatch.setattr(requests, "get", _explode)

    with pytest.raises(ValueError):
        download_logo("http://minio:9000/some-bucket/")


def test_public_https_url_passes_the_guard(monkeypatch):
    """The guard must not reject legitimate logos."""
    import socket

    def _fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    _assert_public_url("https://cdn.example.com/logo.png")


def test_timeout_and_no_redirects_are_passed(monkeypatch):
    """A missing timeout is an unauthenticated DoS; a followed redirect is a bypass."""
    import socket

    import requests

    from fakts.utils import LOGO_CONNECT_TIMEOUT, LOGO_READ_TIMEOUT

    def _fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    captured = {}

    class _Response:
        status_code = 500
        headers = {"Content-Type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_get(url, **kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(requests, "get", _fake_get)

    with pytest.raises(ValueError):
        download_logo("https://cdn.example.com/logo.png")

    assert captured["timeout"] == (LOGO_CONNECT_TIMEOUT, LOGO_READ_TIMEOUT)
    assert captured["allow_redirects"] is False
