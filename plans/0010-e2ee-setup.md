# 0010 — End-to-End Encryption (E2EE) Setup

## Goal

Enable end-to-end encryption so telemente can read and send in encrypted rooms.
Persist the olm/megolm store across restarts, upload device keys, and use a
trust-on-first-use (TOFU) policy for v0.1.0 (no interactive device verification
yet). Send and receive encrypted messages transparently through `MatrixClient`.

## Dependencies

- 0002 (`Paths.store_dir`).
- 0003 (`MatrixClient` — this plan extends it).
- 0009 (live receive path — encrypted messages should flow like plaintext).

## Background

- `matrix-nio[e2e]` is already a dependency; `python-olm` ships a wheel with
  bundled libolm, so **no system libolm is required** on common platforms. CI
  installs `libolm-dev` as a safety net for source builds. E2EE tests are marked
  `@pytest.mark.olm` and **skip if `olm` cannot be imported**.

## Files to create / modify

- `src/telemente/matrix/client.py` — modify: enable the store + encryption.
- `tests/matrix/test_e2ee.py` — new (`@pytest.mark.olm`).
- `README.md` — already documents the libolm fallback; verify it's accurate.

## Behavior / implementation

- **Store**: construct `nio.AsyncClient` with `store_path=str(paths.store_dir)`
  and `config=AsyncClientConfig(store_sync_tokens=True, encryption_enabled=True)`.
  Call `client.load_store()` after login/restore so olm account + device keys
  persist. Ensure `store_dir` exists (via `Paths.ensure()`).
- **Keys**: after the first sync, if `client.should_upload_keys`, `await
  client.keys_upload()`. If `client.should_query_keys`, `await
  client.keys_query()`. Hook these into the sync response handler.
- **TOFU trust (v0.1.0)**: on `MegolmEvent` decryption failures / for outbound
  encryption, auto-trust devices in the room. Implement a helper that, before
  sending to an encrypted room, marks all devices of all members as trusted
  (`client.verify_device` / iterate `client.device_store`). Document clearly in
  code that this is TOFU and **not** secure against MITM — interactive
  verification is a later milestone.
- **Sending**: `send_text` to an encrypted room must use `room_send(...,
  ignore_unverified_devices=True)` (since we TOFU). For unencrypted rooms,
  behavior is unchanged.
- **Receiving**: ensure `RoomMessageText` callbacks fire for decrypted messages
  the same way as plaintext (nio decrypts before dispatching when the store has
  the keys), so 0009's live path needs no change.
- **Key requests**: register `client.add_event_callback` for `MegolmEvent` to
  request keys for undecryptable messages (`client.request_room_key(event)`),
  and render a placeholder ("🔒 unable to decrypt") until keys arrive.

## Test cases (write first)

`tests/matrix/test_e2ee.py` — all `@pytest.mark.olm`, skip if `olm` import fails:

1. `test_store_persists_across_restart` — create a `MatrixClient` with a
   `tmp_store` store_path; simulate login + `load_store`; assert an olm account
   file/store exists in `store_dir`; build a second client on the same path and
   assert the account is reused (same device keys / no new account created).
2. `test_encrypted_room_uses_ignore_unverified` — inject a mock nio client; mark
   a room `encrypted=True`; `send_text` → `room_send` called with
   `ignore_unverified_devices=True`.
3. `test_unencrypted_room_send_unchanged` — `encrypted=False` room → `room_send`
   without the e2e-only flag (or default), unchanged from 0003.
4. `test_keys_uploaded_after_sync` — drive the sync handler with
   `should_upload_keys=True`; assert `keys_upload` awaited.
5. `test_megolm_undecryptable_requests_key` — feed a `MegolmEvent` the client
   can't decrypt; assert `request_room_key` awaited and a placeholder message
   is surfaced.
6. `test_tofu_trusts_room_devices_before_send` — encrypted room with an
   unverified device; before send, the device is marked verified/ignored so the
   send proceeds.

## Mocking strategy

- Most tests inject a **mock nio client** (DI) and assert call patterns —
  no real crypto needed for 2–6. Only test 1 exercises a **real**
  `nio.AsyncClient` store to prove persistence; it is `@pytest.mark.olm` and
  uses `tmp_store`, no network (login can be stubbed with `aioresponses` or by
  directly priming the store API nio exposes).
- Guard the module: `olm = pytest.importorskip("olm")` at top, or a
  `pytestmark = pytest.mark.olm` plus a session-level skip when import fails.
- Never contact a real homeserver; never reuse a real account.

## CI / docs

- CI already installs `libolm-dev` and does not deselect `olm` tests, so they
  run in CI. Locally they skip if libolm/olm is unavailable.
- Confirm `README.md`'s libolm section matches reality (bundled wheel; system
  lib only needed for source builds).

## Done-when

- [ ] All 6 tests pass in CI (with olm available); skip cleanly without olm.
- [ ] Store persists across restarts; keys upload/query wired into sync.
- [ ] Encrypted rooms send with TOFU (`ignore_unverified_devices=True`);
      undecryptable messages show a placeholder and request keys.
- [ ] TOFU limitation documented in code + README.
- [ ] `mypy --strict` + `ruff` clean.
