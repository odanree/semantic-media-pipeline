"""
Unit tests for api/utils.py utility functions.

These are pure functions with no external deps — 100% coverage is easy and
gives a reliable regression signal if the helpers change behaviour.
"""

import importlib.util
import os
import pytest

# api/utils.py is shadowed by the api/utils/ package at import time.
# Load the standalone module directly by file path.
_spec = importlib.util.spec_from_file_location(
    "utils_helpers",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "utils.py"),
)
_utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_utils)

get_env_bool  = _utils.get_env_bool
get_env_int   = _utils.get_env_int
get_env_float = _utils.get_env_float


# ---------------------------------------------------------------------------
# get_env_bool
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("true",  True),
    ("True",  True),
    ("TRUE",  True),
    ("1",     True),
    ("yes",   True),
    ("on",    True),
    ("false", False),
    ("0",     False),
    ("no",    False),
    ("off",   False),
    ("",      False),
])
def test_get_env_bool_values(monkeypatch, value, expected):
    monkeypatch.setenv("TEST_BOOL", value)
    assert get_env_bool("TEST_BOOL") == expected


def test_get_env_bool_default_false(monkeypatch):
    monkeypatch.delenv("TEST_BOOL", raising=False)
    assert get_env_bool("TEST_BOOL") is False


def test_get_env_bool_default_true(monkeypatch):
    monkeypatch.delenv("TEST_BOOL", raising=False)
    assert get_env_bool("TEST_BOOL", default=True) is True


# ---------------------------------------------------------------------------
# get_env_int
# ---------------------------------------------------------------------------

def test_get_env_int_present(monkeypatch):
    monkeypatch.setenv("TEST_INT", "42")
    assert get_env_int("TEST_INT") == 42


def test_get_env_int_default(monkeypatch):
    monkeypatch.delenv("TEST_INT", raising=False)
    assert get_env_int("TEST_INT", default=99) == 99


def test_get_env_int_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("TEST_INT", "not-a-number")
    assert get_env_int("TEST_INT", default=7) == 7


def test_get_env_int_negative(monkeypatch):
    monkeypatch.setenv("TEST_INT", "-5")
    assert get_env_int("TEST_INT") == -5


# ---------------------------------------------------------------------------
# get_env_float
# ---------------------------------------------------------------------------

def test_get_env_float_present(monkeypatch):
    monkeypatch.setenv("TEST_FLOAT", "3.14")
    assert abs(get_env_float("TEST_FLOAT") - 3.14) < 1e-9


def test_get_env_float_default(monkeypatch):
    monkeypatch.delenv("TEST_FLOAT", raising=False)
    assert get_env_float("TEST_FLOAT", default=1.5) == 1.5


def test_get_env_float_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("TEST_FLOAT", "NaN-not-float")
    assert get_env_float("TEST_FLOAT", default=2.0) == 2.0


def test_get_env_float_zero(monkeypatch):
    monkeypatch.setenv("TEST_FLOAT", "0.0")
    assert get_env_float("TEST_FLOAT") == 0.0
