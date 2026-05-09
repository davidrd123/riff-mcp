import json

import pytest

from riff_mcp_doctor import doctor


def test_check_env_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_ENV_KEY", "secret-value")
    result = doctor.check_env("FAKE_ENV_KEY", required_for=["unit-test"])
    assert result.status == "ok"
    assert "12 chars" in result.detail
    assert result.required_for == ["unit-test"]


def test_check_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAKE_ENV_KEY", raising=False)
    result = doctor.check_env("FAKE_ENV_KEY", required_for=["unit-test"])
    assert result.status == "fail"
    assert result.detail == "not set"


def test_check_python_pkg_present() -> None:
    result = doctor.check_python_pkg("json", required_for=["unit-test"])
    assert result.status == "ok"


def test_check_python_pkg_missing() -> None:
    result = doctor.check_python_pkg(
        "definitely_not_a_real_pkg_42", required_for=["unit-test"]
    )
    assert result.status == "fail"
    assert result.detail == "not importable"


def test_check_binary_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/fake")
    result = doctor.check_binary("fake", required_for=["unit-test"])
    assert result.status == "ok"
    assert result.detail == "/usr/bin/fake"


def test_check_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    result = doctor.check_binary("fake", required_for=["unit-test"])
    assert result.status == "fail"


def test_run_all_checks_no_network() -> None:
    results = doctor.run_all_checks(network=False)
    categories = {r.category for r in results}
    assert "env" in categories
    assert "python" in categories
    assert "binary" in categories
    assert "network" not in categories


def test_run_all_checks_with_network_skipped_when_no_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(doctor.GEMINI_KEY, raising=False)
    monkeypatch.delenv(doctor.REPLICATE_TOKEN, raising=False)
    results = doctor.run_all_checks(network=True)
    network = [r for r in results if r.category == "network"]
    assert len(network) == 2
    assert all(r.status == "skipped" for r in network)


def test_format_text_marks_failures() -> None:
    results = [
        doctor.CheckResult("OK_VAR", "env", "ok", "set", ["x"]),
        doctor.CheckResult("MISSING_VAR", "env", "fail", "not set", ["x"]),
    ]
    text = doctor.format_text(results)
    assert "All checks passed." not in text
    assert "1 check(s) failed:" in text
    assert "MISSING_VAR: not set" in text


def test_format_text_all_ok() -> None:
    results = [
        doctor.CheckResult("OK_VAR", "env", "ok", "set", ["x"]),
    ]
    text = doctor.format_text(results)
    assert "All checks passed." in text


def test_format_json_round_trips() -> None:
    results = [doctor.CheckResult("X", "env", "ok", "set", ["a", "b"])]
    parsed = json.loads(doctor.format_json(results))
    assert parsed == {
        "results": [
            {
                "name": "X",
                "category": "env",
                "status": "ok",
                "detail": "set",
                "required_for": ["a", "b"],
            }
        ]
    }


def test_main_returns_zero_when_all_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = [doctor.CheckResult("OK", "env", "ok", "set", [])]
    monkeypatch.setattr(doctor, "run_all_checks", lambda *, network=False: fake)
    rc = doctor.main([])
    assert rc == 0


def test_main_returns_one_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = [
        doctor.CheckResult("OK", "env", "ok", "set", []),
        doctor.CheckResult("BAD", "env", "fail", "not set", []),
    ]
    monkeypatch.setattr(doctor, "run_all_checks", lambda *, network=False: fake)
    rc = doctor.main([])
    assert rc == 1
