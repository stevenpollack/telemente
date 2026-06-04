"""Matrix protocol layer: a thin async wrapper around matrix-nio.

The TUI never talks to matrix-nio directly; it goes through this package so the
network/protocol concerns stay isolated and mockable. See
``plans/0003-matrix-client-wrapper.md``.
"""
