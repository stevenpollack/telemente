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

| # | Plan | Summary |
|---|------|---------|
| 0001 | [project-scaffolding](0001-project-scaffolding.md) | Repo skeleton, tooling, CI (baseline, already built) |
| 0002 | [config-and-credentials](0002-config-and-credentials.md) | XDG paths, settings, secure session storage |
| 0003 | [matrix-client-wrapper](0003-matrix-client-wrapper.md) | Async `MatrixClient` over matrix-nio + models |
| 0004 | [login-screen](0004-login-screen.md) | Textual login form |
| 0005 | [main-screen-layout](0005-main-screen-layout.md) | Three-panel collapsible layout |
| 0006 | [room-list-panel](0006-room-list-panel.md) | Searchable/filterable room list |
| 0007 | [message-view-panel](0007-message-view-panel.md) | Timeline + composer |
| 0008 | [member-list-panel](0008-member-list-panel.md) | Room member list |
| 0009 | [sync-state-integration](0009-sync-state-integration.md) | Wire nio sync into Textual |
| 0010 | [e2ee-setup](0010-e2ee-setup.md) | End-to-end encryption (libolm/olm store) |
| 0011 | [sso-login](0011-sso-login.md) | SSO login with dynamic flow detection (+ manual fallback) |
