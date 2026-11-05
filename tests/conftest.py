"""Shared fixtures and a deterministic environment for the tests."""

from __future__ import annotations

import os

import pytest

# Pin sources of nondeterminism (this must also be set in CI).
os.environ.setdefault("PYTHONHASHSEED", "0")
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
    os.environ.setdefault(_v, "1")


@pytest.fixture
def tmp_run_dir(tmp_path):
    return tmp_path / "run"
