"""Unit tests for the engine's pure logic (no device, no network)."""

from __future__ import annotations

import json

import pytest

from verifyr.api_check import _dig, fetch_api_value
from verifyr.buildinfo import version_lt
from verifyr.checks import Check
from verifyr.parity import _loose_match, _norm


# ---- buildinfo.version_lt ----
@pytest.mark.parametrize(
    "installed,required,expected",
    [
        ("1.5.0", "1.6.0", True),
        ("1.6.0", "1.6.0", False),
        ("1.6.1", "1.6.0", False),
        ("1.10.0", "1.9.0", False),  # numeric, not lexical
        ("1.2", "1.2.1", True),
        (None, "1.6.0", False),      # unknown installed -> don't claim older
        ("1.6.0", None, False),
        ("abc", "1.0", False),       # unparseable -> no claim
    ],
)
def test_version_lt(installed, required, expected):
    assert version_lt(installed, required) is expected


# ---- api_check._dig (json_path extraction) ----
def test_dig_dotted_path():
    data = {"bitcoin": {"usd": 65000}}
    assert _dig(data, "bitcoin.usd") == 65000


def test_dig_with_index():
    data = {"data": {"products": [{"price": "9.99"}, {"price": "19.99"}]}}
    assert _dig(data, "data.products[1].price") == "19.99"


def test_dig_missing_key_raises():
    with pytest.raises(KeyError):
        _dig({"a": 1}, "b")


def test_dig_index_on_non_list_raises():
    with pytest.raises(KeyError):
        _dig({"a": 1}, "a[0]")


# ---- api_check.fetch_api_value (urlopen stubbed) ----
class _FakeResp:
    def __init__(self, body: str):
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_api_value_happy(monkeypatch):
    monkeypatch.setattr(
        "verifyr.api_check.urllib.request.urlopen",
        lambda *a, **k: _FakeResp(json.dumps({"bitcoin": {"usd": 65000}})),
    )
    res = fetch_api_value("https://api.example/price", "bitcoin.usd")
    assert res.ok is True
    assert res.value == "65000"


def test_fetch_api_value_path_not_found(monkeypatch):
    monkeypatch.setattr(
        "verifyr.api_check.urllib.request.urlopen",
        lambda *a, **k: _FakeResp(json.dumps({"bitcoin": {"usd": 65000}})),
    )
    res = fetch_api_value("https://api.example/price", "ethereum.usd")
    assert res.ok is False
    assert "not found" in (res.error or "")


def test_fetch_api_value_non_json(monkeypatch):
    monkeypatch.setattr(
        "verifyr.api_check.urllib.request.urlopen",
        lambda *a, **k: _FakeResp("<html>nope</html>"),
    )
    res = fetch_api_value("https://api.example/price", "x")
    assert res.ok is False
    assert "not JSON" in (res.error or "")


# ---- parity normalization / loose match ----
@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("$1,099.00", "1099.00", True),     # currency/punctuation stripped
        ("  65000 ", "65000", True),
        ("BTC", "btc", True),               # case-insensitive
        ("100", "101", False),
        (None, "100", False),
    ],
)
def test_loose_match(a, b, expected):
    assert _loose_match(a, b) is expected


def test_norm_strips_symbols():
    assert _norm("$1,099.00") == "109900"


# ---- checks store schema ----
def test_check_from_dict_and_android_target():
    check = Check.from_dict(
        {
            "name": "BTC price",
            "web": {"url": "https://x", "selector": ".price"},
            "api": {"endpoint": "https://api", "json_path": "bitcoin.usd"},
            "app_targets": [
                {"platform": "ios", "package": "com.x.ios", "goal": "g-ios"},
                {"platform": "android", "package": "com.x", "goal": "g", "label": "BTC"},
            ],
        }
    )
    assert check.name == "BTC price"
    assert check.web.selector == ".price"
    assert check.api.json_path == "bitcoin.usd"
    # android_target prefers the android entry even when it's not first.
    t = check.android_target()
    assert t.platform == "android"
    assert t.package == "com.x"


def test_check_android_target_falls_back_to_first():
    check = Check.from_dict(
        {"name": "n", "app_targets": [{"platform": "ios", "package": "com.x.ios"}]}
    )
    assert check.android_target().platform == "ios"


def test_check_no_targets():
    check = Check.from_dict({"name": "n"})
    assert check.android_target() is None
