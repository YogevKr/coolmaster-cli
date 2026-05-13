import os

from coolmaster_cli.cli import _doctor_summary, _load_dotenv, _parse_key_values


def test_parse_key_values() -> None:
    assert _parse_key_values(">version       : 1.5.2B\nmelody        : Silence\nOK\n>") == {
        "version": "1.5.2B",
        "melody": "Silence",
    }


def test_doctor_summary_prefers_fail_then_warn() -> None:
    assert _doctor_summary([{"status": "pass"}, {"status": "warn"}])["status"] == "warn"
    assert _doctor_summary([{"status": "pass"}, {"status": "fail"}])["status"] == "fail"
    assert _doctor_summary([{"status": "pass"}, {"status": "skip"}])["status"] == "pass"


def test_load_dotenv_sets_missing_values(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "export COOLMASTER_HOST=192.0.2.10",
                "COOLMASTER_ASCII_PORT=10102",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("COOLMASTER_HOST", raising=False)
    monkeypatch.setenv("COOLMASTER_ASCII_PORT", "11111")

    _load_dotenv(env_path)

    assert os.environ["COOLMASTER_HOST"] == "192.0.2.10"
    assert os.environ["COOLMASTER_ASCII_PORT"] == "11111"
