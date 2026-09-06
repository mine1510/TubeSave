"""DNS helpers for networks that block YouTube via traditional DNS.

Some ISPs return NXDOMAIN for www.youtube.com while HTTPS DoH still works.
curl_cffi/libcurl does not use Python's getaddrinfo, so we enable CURLOPT_DOH_URL
for yt-dlp's curl backend and fall back to DoH for Python sockets (urllib).
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.request
from typing import Any

_DOH_URL = "https://cloudflare-dns.com/dns-query"
_DOH_JSON = "https://cloudflare-dns.com/dns-query?name={host}&type={qtype}"
_INSTALLED = False
_LOCK = threading.Lock()
_CACHE: dict[tuple[str, int], list[str]] = {}
_ORIG_GETADDRINFO = socket.getaddrinfo


def _doh_lookup(host: str, qtype: str = "A") -> list[str]:
    key = (host.lower().rstrip("."), 1 if qtype == "A" else 28)
    cached = _CACHE.get(key)
    if cached is not None:
        return list(cached)
    req = urllib.request.Request(
        _DOH_JSON.format(host=key[0], qtype=qtype),
        headers={"Accept": "application/dns-json"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    answers = data.get("Answer") or []
    wanted = 1 if qtype == "A" else 28
    result = [str(item["data"]) for item in answers if item.get("type") == wanted]
    if result:
        _CACHE[key] = list(result)
    return result


def _patched_getaddrinfo(
    host: str | bytes | None,
    port: Any,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
):
    try:
        return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
    except socket.gaierror:
        if not host or isinstance(host, bytes):
            raise
        host_s = str(host).strip(".")
        if not host_s or host_s.replace(".", "").isdigit():
            raise
        ips: list[str] = []
        if family in (0, socket.AF_INET):
            ips.extend(_doh_lookup(host_s, "A"))
        if family in (0, socket.AF_INET6):
            ips.extend(_doh_lookup(host_s, "AAAA"))
        if not ips:
            raise
        results = []
        port_i = int(port) if port else 0
        for ip in ips:
            if ":" in ip:
                if family not in (0, socket.AF_INET6):
                    continue
                sockaddr = (ip, port_i, 0, 0)
                results.append((socket.AF_INET6, type or socket.SOCK_STREAM, proto or 0, "", sockaddr))
            else:
                if family not in (0, socket.AF_INET):
                    continue
                sockaddr = (ip, port_i)
                results.append((socket.AF_INET, type or socket.SOCK_STREAM, proto or 0, "", sockaddr))
        if not results:
            raise
        return results


def _patch_curl_cffi_doh() -> None:
    try:
        from curl_cffi.const import CurlOpt
        from curl_cffi.requests import Session
        from yt_dlp.networking._curlcffi import CurlCFFIRH
    except Exception:
        return

    if getattr(CurlCFFIRH, "_tubesave_doh_patched", False):
        return

    original_create = CurlCFFIRH._create_instance

    def _create_with_doh(self, cookiejar=None):  # type: ignore[no-untyped-def]
        session: Session = original_create(self, cookiejar=cookiejar)
        try:
            # curl_options survive stream=True duphandle()+set_curl_options();
            # a one-shot setopt on session.curl does not.
            options = getattr(session, "curl_options", None)
            if options is None:
                session.curl_options = {}
                options = session.curl_options
            options[CurlOpt.DOH_URL] = _DOH_URL
        except Exception:
            pass
        return session

    CurlCFFIRH._create_instance = _create_with_doh  # type: ignore[method-assign]
    CurlCFFIRH._tubesave_doh_patched = True


def install_doh_dns() -> None:
    """Enable DoH for curl_cffi (yt-dlp) and Python sockets. Safe to call repeatedly."""
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        _patch_curl_cffi_doh()
        socket.getaddrinfo = _patched_getaddrinfo  # type: ignore[assignment]
        _INSTALLED = True
