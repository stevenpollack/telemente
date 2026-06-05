"""Conftest for matrix tests.

Patches aioresponses to work with aiohttp >= 3.10 which added the
``stream_writer`` required keyword argument to ``ClientResponse.__init__``.
aioresponses 0.7.8 predates this change; this shim fills the gap.

When upgrading aioresponses: verify whether the upstream release includes its own
aiohttp 3.10+ compat fix. If it does, delete ``_patched_build_response`` and the
``patch_aioresponses_build`` fixture and pin to the newer version.
Track at: https://github.com/pnuckowski/aioresponses/issues
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import Mock

import aiohttp.hdrs as hdrs
import nio
import pytest
from aiohttp import ClientResponse
from aiohttp.helpers import TimerNoop
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from matrix.helpers import HOMESERVER, USER


def _patched_build_response(
    self: Any,
    url: URL | str,
    method: str = hdrs.METH_GET,
    request_headers: dict[str, str] | None = None,
    status: int = 200,
    body: str | bytes = "",
    content_type: str = "application/json",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    response_class: type[ClientResponse] | None = None,
    reason: str | None = None,
) -> ClientResponse:
    """Replacement for aioresponses._build_response compatible with aiohttp 3.10+."""

    from aiohttp.streams import StreamReader

    if response_class is None:
        response_class = ClientResponse
    if payload is not None:
        body = json.dumps(payload)
    if not isinstance(body, bytes):
        body = str.encode(body)
    if request_headers is None:
        request_headers = {}

    loop = Mock()
    loop.get_debug = Mock(return_value=True)

    # Build minimal stream writer mock
    stream_writer_mock = Mock()
    stream_writer_mock.output_size = 0

    kwargs: dict[str, Any] = {}
    from aiohttp import RequestInfo

    if isinstance(url, str):
        url = URL(url)
    kwargs["request_info"] = RequestInfo(
        url=url,
        method=method,
        headers=CIMultiDictProxy(CIMultiDict(**request_headers)),
        real_url=url,
    )
    kwargs["writer"] = None
    kwargs["continue100"] = None
    kwargs["timer"] = TimerNoop()
    kwargs["traces"] = []
    kwargs["loop"] = loop
    kwargs["session"] = None
    kwargs["stream_writer"] = stream_writer_mock

    _headers = CIMultiDict({hdrs.CONTENT_TYPE: content_type})
    if headers:
        _headers.update(headers)

    raw_headers = tuple((k.encode("utf-8"), v.encode("utf-8")) for k, v in _headers.items())

    if reason is None:
        reason = "OK" if status == 200 else "Error"

    resp = response_class(method, url, **kwargs)

    for hdr in _headers.getall(hdrs.SET_COOKIE, ()):
        resp.cookies.load(hdr)

    resp._headers = _headers  # type: ignore[assignment]
    resp._raw_headers = raw_headers  # pyright: ignore[reportPrivateUsage]  # aiohttp compat shim
    resp.status = status
    resp.reason = reason

    # Feed body into content stream
    resp.content = StreamReader(protocol=Mock(), limit=2**16, loop=loop)
    resp.content.feed_data(body)
    resp.content.feed_eof()
    return resp


@pytest.fixture(autouse=True)
def patch_aioresponses_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch aioresponses to work with aiohttp 3.10+."""
    import aioresponses.core as core

    monkeypatch.setattr(core.RequestMatch, "_build_response", _patched_build_response)


@pytest.fixture
async def real_nio_client() -> AsyncGenerator[nio.AsyncClient, None]:
    """Real ``nio.AsyncClient`` with in-memory store; no live homeserver."""
    client = nio.AsyncClient(HOMESERVER, USER)
    yield client
    await client.close()
