"""Kairo-30 benchmark harness.

Every task is a real bug (or a real non-bug) from production AI infrastructure,
pinned to the upstream commit where it was reproduced. The harness runs a coding
agent inside that environment, extracts its patch, and grades the patch with a
hidden, offline, deterministic verifier.
"""

__version__ = "0.1.0"
