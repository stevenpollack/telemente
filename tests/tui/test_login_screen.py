"""Tests for LoginScreen (plan 0004 / updated plan 0011).

All tests inject FakeMatrixClient — no real network.
A tiny host App pushes LoginScreen and records LoggedIn messages.

Plan 0011 change: LoginScreen now takes a ``client_factory: Callable[[str],
_LoginClient]`` instead of a pre-built client. Tests pass a factory that
always returns the same FakeMatrixClient, keeping existing password-login
tests working without modification to assertions.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Label, LoadingIndicator, Static

import fakes as fakes_module
from telemente.config import Session
from telemente.matrix.auth import LoginFlows
from telemente.tui.screens.login import LoginScreen

FakeMatrixClient = fakes_module.FakeMatrixClient

# ---------------------------------------------------------------------------
# Minimal host app that records LoggedIn messages
# ---------------------------------------------------------------------------


class HostApp(App[None]):
    """Minimal app that records LoginScreen.LoggedIn messages for assertions."""

    def __init__(self, client: FakeMatrixClient, *, default_homeserver: str) -> None:
        super().__init__()
        self._client = client
        self._default_homeserver = default_homeserver
        self.logged_in_sessions: list[Session] = []

    def on_mount(self) -> None:
        # Plan 0011: LoginScreen takes a factory, not a pre-built client.
        # Wrap the fake in a factory that ignores the homeserver argument.
        def factory(_homeserver: str) -> FakeMatrixClient:
            return self._client

        self.push_screen(LoginScreen(factory, default_homeserver=self._default_homeserver))

    def on_login_screen_logged_in(self, message: LoginScreen.LoggedIn) -> None:
        self.logged_in_sessions.append(message.session)

    def compose(self) -> ComposeResult:
        yield Label("host")


def _make_host_app(
    fake: FakeMatrixClient,
    default_homeserver: str = "https://matrix.org",
) -> HostApp:
    """Return a HostApp wired to push LoginScreen with the given fake client."""
    # Give the fake password-only flows so the password form is shown
    fake.set_flows(LoginFlows(password=True, sso=False, token=False))
    return HostApp(fake, default_homeserver=default_homeserver)


# ---------------------------------------------------------------------------
# Test 1: successful login posts LoggedIn
# ---------------------------------------------------------------------------


async def test_successful_login_posts_loggedin() -> None:
    fake = FakeMatrixClient()
    app = _make_host_app(fake)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()  # let mount + screen push settle

        screen = app.screen
        screen.query_one("#homeserver", Input).value = "https://matrix.org"
        screen.query_one("#username", Input).value = "@alice:matrix.org"
        screen.query_one("#password", Input).value = "s3cret"

        await pilot.click("#submit")
        await pilot.pause()
        await pilot.pause()

    assert len(app.logged_in_sessions) == 1
    session = app.logged_in_sessions[0]
    assert session.user_id == "@alice:matrix.org"
    assert fake.login_called is True


# ---------------------------------------------------------------------------
# Test 2: failed login shows error inline, stays on LoginScreen
# ---------------------------------------------------------------------------


async def test_failed_login_shows_error() -> None:
    fake = FakeMatrixClient()
    fake.login_should_fail = True
    app = _make_host_app(fake)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        screen = app.screen
        screen.query_one("#homeserver", Input).value = "https://matrix.org"
        screen.query_one("#username", Input).value = "@alice:matrix.org"
        screen.query_one("#password", Input).value = "wrong"

        await pilot.click("#submit")
        await pilot.pause()
        await pilot.pause()

        error_widget = screen.query_one("#error", Static)
        assert error_widget.display is True
        assert "Scripted login failure" in str(error_widget.content)

    # No navigation happened
    assert len(app.logged_in_sessions) == 0


# ---------------------------------------------------------------------------
# Test 3: empty fields block network call
# ---------------------------------------------------------------------------


async def test_empty_fields_blocks_network() -> None:
    fake = FakeMatrixClient()
    app = _make_host_app(fake)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        screen = app.screen
        screen.query_one("#homeserver", Input).value = "https://matrix.org"
        screen.query_one("#username", Input).value = "@alice:matrix.org"
        # password left empty

        await pilot.click("#submit")
        await pilot.pause()

        error_widget = screen.query_one("#error", Static)
        assert error_widget.display is True
        assert "All fields are required" in str(error_widget.content)

    assert fake.login_called is False


# ---------------------------------------------------------------------------
# Test 4: pressing Enter in password field submits
# ---------------------------------------------------------------------------


async def test_enter_in_password_submits() -> None:
    fake = FakeMatrixClient()
    app = _make_host_app(fake)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        screen = app.screen
        screen.query_one("#homeserver", Input).value = "https://matrix.org"
        screen.query_one("#username", Input).value = "@alice:matrix.org"
        screen.query_one("#password", Input).value = "s3cret"

        # Focus the password field and press Enter
        await pilot.click("#password")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

    assert len(app.logged_in_sessions) == 1
    assert fake.login_called is True


# ---------------------------------------------------------------------------
# Test 5: loading state — form disabled while awaiting login
# ---------------------------------------------------------------------------


async def test_loading_state() -> None:
    fake = FakeMatrixClient()
    fake.login_should_block = True
    app = _make_host_app(fake)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        screen = app.screen
        screen.query_one("#homeserver", Input).value = "https://matrix.org"
        screen.query_one("#username", Input).value = "@alice:matrix.org"
        screen.query_one("#password", Input).value = "s3cret"

        await pilot.click("#submit")
        await pilot.pause()

        # While blocking: loading indicator visible, button disabled
        loading = screen.query_one("#loading", LoadingIndicator)
        assert loading.display is True

        submit_btn = screen.query_one("#submit", Button)
        assert submit_btn.disabled is True

        # Release and let it finish
        fake.unblock_login()
        await pilot.pause()
        await pilot.pause()

    assert len(app.logged_in_sessions) == 1
