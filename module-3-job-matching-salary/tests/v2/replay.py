"""Record live module 1 / module 2 HTTP calls once, replay them offline (tests/v2/fixtures/*.json).

Requests are keyed by method, path, sorted query parameters (keys and tracking ids never recorded: module 1 and 2
take none) and a hash of the JSON body. An unrecorded request in replay is a 599 with the key, so a test fails
loudly instead of reaching the network.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qsl

import httpx


def request_key(request: httpx.Request) -> str:
    params = sorted(parse_qsl(request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query))
    body = request.content or b""
    digest = hashlib.sha1(body).hexdigest()[:16] if body else ""
    return f"{request.method} {request.url.path}?{'&'.join(f'{k}={v}' for k, v in params)} #{digest}"


class RecordingTransport(httpx.BaseTransport):
    def __init__(self):
        self.inner = httpx.HTTPTransport()
        self.recorded: dict[str, dict] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        response.read()
        self.recorded[request_key(request)] = {"status": response.status_code, "json": response.json()
                                               if response.headers.get("content-type", "").startswith("application/json")
                                               else None}
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)


class ReplayTransport(httpx.BaseTransport):
    def __init__(self, recorded: dict[str, dict]):
        self.recorded = recorded
        self.misses: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key = request_key(request)
        hit = self.recorded.get(key)
        if hit is None:
            self.misses.append(key)
            return httpx.Response(599, json={"error": f"not recorded: {key}"})
        return httpx.Response(hit["status"], json=hit["json"])


def strip_tracking(url: str | None) -> str | None:
    """Adzuna redirect URLs carry tracking parameters: keep scheme, host and path only."""
    return url.split("?")[0] if url else url
