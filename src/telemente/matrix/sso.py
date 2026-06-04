"""SSO loopback callback server (plan 0011).

``SsoCallbackServer`` binds a loopback aiohttp HTTP server on 127.0.0.1 at an
ephemeral port and waits for the homeserver to redirect the user's browser back
to it with a ``?loginToken=<token>`` query parameter.

Security notes:
- Strictly loopback — binds 127.0.0.1, never 0.0.0.0.
- The redirect path includes a random nonce (``secrets.token_urlsafe``); only a
  request to that exact path resolves the token, preventing a stray browser tab
  from completing the flow.
- The ``loginToken`` is single-use and short-lived — NEVER log it.
"""

from __future__ import annotations

import asyncio
import logging
import secrets

from aiohttp import web

logger = logging.getLogger(__name__)


class SsoError(Exception):
    """Base exception for SSO errors."""


class SsoTimeoutError(SsoError):
    """Raised when the SSO flow times out waiting for the callback."""


class SsoCallbackServer:
    """Loopback HTTP server that captures the SSO loginToken redirect.

    Usage::

        server = SsoCallbackServer()
        redirect_url = await server.start()  # e.g. http://localhost:PORT/<nonce>
        # ... open browser to the SSO URL with redirect_url as the callback ...
        token = await server.wait_for_token(timeout=300.0)
        await server.stop()

    The nonce path prevents an unrelated request from resolving the token.
    Strictly loopback — no external network access.
    """

    def __init__(self) -> None:
        self._nonce: str = secrets.token_urlsafe(32)
        self._future: asyncio.Future[str] | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None

    async def start(self) -> str:
        """Start the loopback server and return the redirect URL.

        The returned URL is ``http://localhost:{port}/{nonce}``.
        """
        self._future = asyncio.get_event_loop().create_future()

        app = web.Application()
        # Register the nonce route — only requests to this exact path are accepted
        app.router.add_get(f"/{self._nonce}", self._handle_callback)

        self._runner = web.AppRunner(app)
        await self._runner.setup()

        # Bind to 127.0.0.1 on an ephemeral port
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()

        # Retrieve the actual bound port
        sockets = self._runner.addresses
        if not sockets:
            raise SsoError("Could not bind loopback server — no sockets available")
        # addresses returns list of (host, port) for TCP sites
        _host, port = sockets[0]
        self._port = int(port)

        redirect_url = f"http://localhost:{self._port}/{self._nonce}"
        logger.debug("SSO callback server started on %s", redirect_url)
        return redirect_url

    async def _handle_callback(self, request: web.Request) -> web.Response:
        """Handle the SSO redirect callback from the homeserver."""
        # SECURITY: never log the loginToken
        token = request.query.get("loginToken")
        if not token:
            return web.Response(status=400, text="Missing loginToken parameter")

        if self._future is not None and not self._future.done():
            self._future.set_result(token)

        html = (
            "<html><body>"
            "<h1>Login complete</h1>"
            "<p>You can close this tab and return to telemente.</p>"
            "</body></html>"
        )
        return web.Response(status=200, content_type="text/html", text=html)

    async def wait_for_token(self, timeout: float = 300.0) -> str:  # noqa: ASYNC109
        """Wait for the browser to deliver the loginToken.

        Parameters
        ----------
        timeout:
            Maximum seconds to wait. Raises ``SsoTimeoutError`` on expiry.

        Returns
        -------
        str
            The raw ``loginToken`` to exchange with the homeserver.

        Raises
        ------
        SsoTimeoutError
            When no callback is received within ``timeout`` seconds.
        """
        if self._future is None:
            raise SsoError("Server not started — call start() first")
        try:
            return await asyncio.wait_for(asyncio.shield(self._future), timeout=timeout)
        except TimeoutError as exc:
            raise SsoTimeoutError("SSO login timed out waiting for callback") from exc

    async def stop(self) -> None:
        """Stop the loopback server. Idempotent."""
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception as exc:
                logger.debug("SsoCallbackServer cleanup error (suppressed): %s", exc)
            finally:
                self._runner = None
                self._site = None
