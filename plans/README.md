# Implementation Plans

This directory contains the detailed, per-feature implementation plans for
telemente v0.1.0. Each plan is **self-contained** and written for handoff to an
implementation agent. Implement them in dependency order.

## Conventions

Every plan document uses these sections:

- **Goal** — one paragraph: what this delivers and why.
- **Dependencies** — which other plans must be done first.
- **Files to create / modify** — exact paths.
- **Public interface** — typed signatures the rest of the app relies on.
- **Behavior** — what it does, edge cases, error handling.
- **Test cases (write first)** — the TDD checklist; write these before code.
- **Mocking strategy** — how to avoid real network / real libolm.
- **Done-when** — the checklist that defines "complete".

## Non-negotiables (see `AGENTS.md`)

- UI never imports `nio`; it goes through `telemente.matrix.client.MatrixClient`.
- No `nio` types cross the `matrix/` boundary — return `matrix/models.py` dataclasses.
- One asyncio loop (Textual's); the sync loop runs as a Textual worker.
- `mypy --strict` and `ruff` must pass; tests never hit a real homeserver.

## Order & dependency graph

```
0001 scaffolding (done)
   └─ 0002 config & credentials
         └─ 0003 matrix client wrapper
               ├─ 0004 login screen
               └─ 0005 main screen layout
                     ├─ 0006 room list panel
                     ├─ 0007 message view panel
                     └─ 0008 member list panel
                           └─ 0009 sync ↔ state integration
                                 └─ 0010 e2ee setup
```

## Index

| # | Plan | Status | Summary |
|---|------|--------|---------|
| 0001 | [project-scaffolding](0001-project-scaffolding.md) | done | Repo skeleton, tooling, CI |
| 0002 | [config-and-credentials](0002-config-and-credentials.md) | done | XDG paths, settings, secure session storage |
| 0003 | [matrix-client-wrapper](0003-matrix-client-wrapper.md) | done | Async `MatrixClient` over matrix-nio + models |
| 0004 | [login-screen](0004-login-screen.md) | done | Textual login form |
| 0005 | [main-screen-layout](0005-main-screen-layout.md) | done | Three-panel collapsible layout |
| 0006 | [room-list-panel](0006-room-list-panel.md) | done | Searchable/filterable room list |
| 0007 | [message-view-panel](0007-message-view-panel.md) | done | Timeline + composer |
| 0008 | [member-list-panel](0008-member-list-panel.md) | done | Room member list |
| 0009 | [sync-state-integration](0009-sync-state-integration.md) | done | Wire nio sync into Textual |
| 0010 | [e2ee-setup](0010-e2ee-setup.md) | done | End-to-end encryption (libolm/olm store) |
| 0011 | [sso-login](0011-sso-login.md) | done | SSO login with dynamic flow detection (+ manual fallback) |
| 0012 | [roomlist-optionlist-migration](0012-roomlist-optionlist-migration.md) | done | Migrate RoomList from ListView to OptionList |
| 0013 | [message-cache](0013-message-cache.md) | done | SQLite write-through message cache |
| 0014 | [log-viewer-panel](0014-log-viewer-panel.md) | done | Log viewer panel (tail telemente.log) |
| 0015 | [nio-type-stubs](0015-nio-type-stubs.md) | done | Partial nio type stubs for mypy + Pyright |
| 0016 | [nio-cassette-integration-tests](0016-nio-cassette-integration-tests.md) | done | nio cassette integration tests with aioresponses |
| 0017 | [matrix-test-blackbox-refactor](0017-matrix-test-blackbox-refactor.md) | done | Matrix test black-box refactor (public API only) |
| 0018 | [test-infrastructure](0018-test-infrastructure.md) | done | Cassette expansion and FakeMatrixClient hardening |
| 0018 | [nio-native-fixture-recording](0018-nio-native-fixture-recording.md) | done | nio-native fixture recording from live Synapse |
| 0019 | [pyright-clean](0019-pyright-clean.md) | done | Pyright zero errors/warnings |
| 0020 | [context-menus](0020-context-menus.md) | done | Context menus and emoji picker |
| 0021 | [context-menu-fixes](0021-context-menu-fixes.md) | done | Context menu and emoji picker bug fixes |
| 0022 | [redacted-message-tombstone](0022-redacted-message-tombstone.md) | done | Redacted message tombstone |
| 0023 | [reply-thread-panel](0023-reply-thread-panel.md) | done | Reply thread panel |
| 0024 | [in-room-message-search](0024-in-room-message-search.md) | done | In-room message search |
| 0025 | [bug-fixes](0025-bug-fixes.md) | done | Post-0024 bug fixes |
| 0026 | [tui-test-refactor](0026-tui-test-refactor.md) | done | TUI test suite refactor: behavioural assertions and snapshot tests |
| 0027 | [textual-emoji-picker-package](0027-textual-emoji-picker-package.md) | done | Extract textual-emoji-picker as a standalone package |
| 0028 | [feature-gap-analysis](0028-feature-gap-analysis.md) | done | Feature gap analysis: telemente vs Element |
| 0029 | [incoming-edits](0029-incoming-edits.md) | pending | Incoming message edits (m.replace live update) |
| 0030 | [incoming-reactions](0030-incoming-reactions.md) | pending | Incoming live reactions (m.reaction callback) |
| 0031 | [read-receipts](0031-read-receipts.md) | done | Read receipt sending |
| 0032a | [virtualize-message-timeline](0032a-virtualize-message-timeline.md) | pending | Virtualize message timeline |
| 0032b | [pagination](0032b-pagination.md) | pending | Pagination (load older messages) |
| 0033 | [join-room](0033-join-room.md) | pending | Join room by ID or alias |
