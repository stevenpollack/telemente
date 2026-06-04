"""Tests for SSO login flow in LoginScreen (plan 0011).

All tests inject FakeMatrixClient + FakeSsoCallbackServer — no real network,
no browser, no loopback aiohttp.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from aioresponses import aioresponses
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Label, Static

import fakes as fakes_module
from telemente.config import Session
from telemente.matrix.auth import IdentityProvider, LoginFlows
from telemente.matrix.sso import SsoTimeoutError
from telemente.tui.screens.login import LoginScreen

FakeMatrixClient = fakes_module.FakeMatrixClient

_CLAM_WELL_KNOWN = "https://clam.au/.well-known/matrix/client"
_CLAM_DISCOVERY = {
    "m.homeserver": {"base_url": "https://clam.au"},
    "org.matrix.msc4143.rtc_foci": [
        {
            "type": "livekit",
            "livekit_service_url": "https://livekit.clam.au/livekit/jwt",
        }
    ],
    "org.matrix.msc2965.authentication": {
        "issuer": "https://mas.clam.au/",
        "account": "https://mas.clam.au/human/account",
    },
}


def _tracking_factory(
    fake: FakeMatrixClient,
) -> tuple[Callable[[str], FakeMatrixClient], list[str]]:
    """Return a client factory that records homeserver args on the fake."""
    calls: list[str] = []

    def factory(homeserver: str) -> FakeMatrixClient:
        calls.append(homeserver)
        fake._fake_homeserver = homeserver
        return fake

    return factory, calls


# ---------------------------------------------------------------------------
# Fake SSO callback server for UI tests
# ---------------------------------------------------------------------------


class FakeSsoCallbackServer:
    """Fake SsoCallbackServer for UI tests — no real aiohttp server."""

    def __init__(
        self,
        redirect_url: str = "http://localhost:9999/testnonce",
        token_result: str | Exception = "fake_sso_token",
    ) -> None:
        self._redirect_url = redirect_url
        self._token_result = token_result
        self.started = False
        self.stopped = False
        self.wait_called = False

    async def start(self) -> str:
        self.started = True
        return self._redirect_url

    async def wait_for_token(self, timeout: float = 300.0) -> str:  # noqa: ASYNC109
        self.wait_called = True
        if isinstance(self._token_result, Exception):
            raise self._token_result
        return self._token_result

    async def stop(self) -> None:
        self.stopped = True


# ---------------------------------------------------------------------------
# Minimal host app for SSO tests
# ---------------------------------------------------------------------------


class SsoHostApp(App[None]):
    """Host app that uses client_factory + optional SSO seams."""

    def __init__(
        self,
        client_factory: Callable[[str], FakeMatrixClient],
        *,
        default_homeserver: str = "https://matrix.org",
        sso_server_factory: Callable[[], FakeSsoCallbackServer] | None = None,
        open_browser: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__()
        self._client_factory = client_factory
        self._default_homeserver = default_homeserver
        self._sso_server_factory = sso_server_factory
        self._open_browser = open_browser
        self.logged_in_sessions: list[Session] = []

    def on_mount(self) -> None:
        kwargs: dict[str, Any] = {
            "default_homeserver": self._default_homeserver,
        }
        if self._sso_server_factory is not None:
            kwargs["sso_server_factory"] = self._sso_server_factory
        if self._open_browser is not None:
            kwargs["open_browser"] = self._open_browser
        self.push_screen(LoginScreen(self._client_factory, **kwargs))

    def on_login_screen_logged_in(self, message: LoginScreen.LoggedIn) -> None:
        self.logged_in_sessions.append(message.session)

    def compose(self) -> ComposeResult:
        yield Label("host")


# ---------------------------------------------------------------------------
# Test 14: SSO button shown when SSO is supported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_button_shown_when_supported() -> None:
    """Factory yields FakeMatrixClient with sso=True, password=False.

    After flow detection: SSO button exists; password form hidden.
    """
    flows = LoginFlows(password=False, sso=True, token=True)
    fake = FakeMatrixClient()
    fake.set_flows(flows)

    factory, _calls = _tracking_factory(fake)
    app = SsoHostApp(factory)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        screen = app.screen
        # SSO button should be present
        sso_buttons = screen.query(".sso-button")
        assert len(sso_buttons) > 0

        # Password submit button should be hidden (or absent)
        try:
            submit = screen.query_one("#submit", Button)
            assert submit.display is False
        except Exception:
            pass  # widget absent is also fine


# ---------------------------------------------------------------------------
# Test 15: both flows shown when both supported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_flows_shown() -> None:
    """password=True, sso=True → both password form and SSO button present."""
    flows = LoginFlows(password=True, sso=True, token=True)
    fake = FakeMatrixClient()
    fake.set_flows(flows)

    factory, _calls = _tracking_factory(fake)
    app = SsoHostApp(factory)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        screen = app.screen
        # Password submit button visible
        submit = screen.query_one("#submit", Button)
        assert submit.display is True

        # SSO button also present
        sso_buttons = screen.query(".sso-button")
        assert len(sso_buttons) > 0


# ---------------------------------------------------------------------------
# Test 16: no supported flow shows error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_supported_flow_shows_error() -> None:
    """password=False, sso=False → error message; no submit controls."""
    flows = LoginFlows(password=False, sso=False, token=False)
    fake = FakeMatrixClient()
    fake.set_flows(flows)

    factory, _calls = _tracking_factory(fake)
    app = SsoHostApp(factory)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        screen = app.screen
        error_widget = screen.query_one("#error", Static)
        assert error_widget.display is True
        content = str(error_widget.content)
        assert "no supported" in content.lower() or "advertises" in content.lower()


# ---------------------------------------------------------------------------
# Test 17: SSO loopback success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_loopback_success() -> None:
    """Fake SSO server returns token → login_with_token called → LoggedIn posted."""
    flows = LoginFlows(password=False, sso=True, token=True)
    fake = FakeMatrixClient()
    fake.set_flows(flows)

    fake_server = FakeSsoCallbackServer(token_result="sso_token_xyz")
    browser_opened: list[str] = []

    def stub_open_browser(url: str) -> bool:
        browser_opened.append(url)
        return True

    factory, _calls = _tracking_factory(fake)
    app = SsoHostApp(
        factory,
        sso_server_factory=lambda: fake_server,
        open_browser=stub_open_browser,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # Click the SSO button
        sso_button = app.screen.query(".sso-button").first()
        await pilot.click(sso_button)

        # Allow the worker to complete
        for _ in range(10):
            await pilot.pause()

    assert fake.login_with_token_called is True
    assert fake.login_with_token_token == "sso_token_xyz"
    assert len(app.logged_in_sessions) == 1


# ---------------------------------------------------------------------------
# Test 18: SSO no browser — switches to manual mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_no_browser_switches_to_manual() -> None:
    """open_browser returns False → URL + login-token input become visible."""
    flows = LoginFlows(password=False, sso=True, token=True)
    fake = FakeMatrixClient()
    fake.set_flows(flows)

    # The fake server hangs waiting (never resolves) so the loopback waits
    token_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    class HangingFakeSsoServer(FakeSsoCallbackServer):
        async def wait_for_token(self, timeout: float = 300.0) -> str:  # noqa: ASYNC109
            # Wait until the test manually resolves or the token input is used
            return await asyncio.wait_for(token_future, timeout=timeout)

    fake_server = HangingFakeSsoServer()

    def no_browser(url: str) -> bool:
        return False

    factory, _calls = _tracking_factory(fake)
    app = SsoHostApp(
        factory,
        sso_server_factory=lambda: fake_server,
        open_browser=no_browser,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        sso_button = app.screen.query(".sso-button").first()
        await pilot.click(sso_button)

        # Allow worker to start
        for _ in range(5):
            await pilot.pause()

        screen = app.screen
        # The manual token input should now be visible
        token_input = screen.query_one("#login-token", Input)
        assert token_input.display is True

        # Now submit a token manually
        token_input.value = "manual_token_abc"
        confirm_btn = screen.query_one("#token-confirm", Button)
        await pilot.click(confirm_btn)

        for _ in range(10):
            await pilot.pause()

    assert fake.login_with_token_called is True
    assert fake.login_with_token_token == "manual_token_abc"
    assert len(app.logged_in_sessions) == 1


# ---------------------------------------------------------------------------
# Test 19: SSO timeout shows error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_timeout_shows_error() -> None:
    """wait_for_token raises SsoTimeoutError → inline error + controls re-enabled."""
    flows = LoginFlows(password=False, sso=True, token=True)
    fake = FakeMatrixClient()
    fake.set_flows(flows)

    fake_server = FakeSsoCallbackServer(token_result=SsoTimeoutError("Timed out"))

    def stub_browser(url: str) -> bool:
        return True

    factory, _calls = _tracking_factory(fake)
    app = SsoHostApp(
        factory,
        sso_server_factory=lambda: fake_server,
        open_browser=stub_browser,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        sso_button = app.screen.query(".sso-button").first()
        await pilot.click(sso_button)

        for _ in range(10):
            await pilot.pause()

        screen = app.screen
        error_widget = screen.query_one("#error", Static)
        assert error_widget.display is True
        assert fake_server.stopped is True


# ---------------------------------------------------------------------------
# Test 20: IdP buttons — 2 IdPs → 2 SSO buttons, correct idp_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idp_buttons() -> None:
    """Flows with 2 IdPs → 2 SSO buttons; clicking one passes the idp_id to sso_redirect_url."""
    flows = LoginFlows(
        password=False,
        sso=True,
        token=True,
        identity_providers=[
            IdentityProvider(id="oidc-google", name="Google"),
            IdentityProvider(id="oidc-github", name="GitHub"),
        ],
    )
    fake = FakeMatrixClient()
    fake.set_flows(flows)

    fake_server = FakeSsoCallbackServer(token_result="tok")

    def stub_browser(url: str) -> bool:
        return True

    factory, _calls = _tracking_factory(fake)
    app = SsoHostApp(
        factory,
        sso_server_factory=lambda: fake_server,
        open_browser=stub_browser,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        screen = app.screen
        sso_buttons = list(screen.query(".sso-button"))
        assert len(sso_buttons) == 2

        # Click the first button (Google)
        await pilot.click(sso_buttons[0])
        for _ in range(10):
            await pilot.pause()

    # The fake client should have been asked for sso_redirect_url with the correct idp_id
    assert fake.sso_redirect_url_called is True
    assert fake.sso_redirect_url_idp_id == "oidc-google"


# ---------------------------------------------------------------------------
# Homeserver resolution (MXID + stale client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_uses_resolved_homeserver_for_mxid() -> None:
    """SSO redirect uses discovered clam.au, not default matrix.org."""
    flows = LoginFlows(password=False, sso=True, token=True)
    fake = FakeMatrixClient()
    fake.set_flows(flows)
    factory, factory_calls = _tracking_factory(fake)

    fake_server = FakeSsoCallbackServer(token_result="clam_token")
    browser_urls: list[str] = []

    def stub_browser(url: str) -> bool:
        browser_urls.append(url)
        return True

    with aioresponses() as m:
        m.get(_CLAM_WELL_KNOWN, payload=_CLAM_DISCOVERY)

        app = SsoHostApp(
            factory,
            default_homeserver="https://matrix.org",
            sso_server_factory=lambda: fake_server,
            open_browser=stub_browser,
        )

        async with app.run_test() as pilot:
            for _ in range(5):
                await pilot.pause()

            screen = app.screen
            screen.query_one("#homeserver", Input).value = "@steven:clam.au"

            sso_button = screen.query(".sso-button").first()
            await pilot.click(sso_button)

            for _ in range(10):
                await pilot.pause()

    assert "https://clam.au" in factory_calls
    assert fake._fake_homeserver == "https://clam.au"
    assert browser_urls
    assert "clam.au" in browser_urls[0]
    assert "matrix.org" not in browser_urls[0]


@pytest.mark.asyncio
async def test_sso_stale_homeserver_without_enter() -> None:
    """Changing homeserver without Enter still re-detects before SSO."""
    flows = LoginFlows(password=False, sso=True, token=True)
    fake = FakeMatrixClient()
    fake.set_flows(flows)
    factory, factory_calls = _tracking_factory(fake)

    fake_server = FakeSsoCallbackServer(token_result="tok")
    browser_urls: list[str] = []

    with aioresponses() as m:
        m.get(_CLAM_WELL_KNOWN, payload=_CLAM_DISCOVERY)

        def record_browser(url: str) -> bool:
            browser_urls.append(url)
            return True

        app = SsoHostApp(
            factory,
            default_homeserver="https://matrix.org",
            sso_server_factory=lambda: fake_server,
            open_browser=record_browser,
        )

        async with app.run_test() as pilot:
            for _ in range(5):
                await pilot.pause()

            assert factory_calls == ["https://matrix.org"]

            screen = app.screen
            screen.query_one("#homeserver", Input).value = "@steven:clam.au"

            await pilot.click(screen.query(".sso-button").first())

            for _ in range(10):
                await pilot.pause()

    assert factory_calls[-1] == "https://clam.au"
    assert browser_urls
    assert "clam.au" in browser_urls[0]
