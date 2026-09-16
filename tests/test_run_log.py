"""Test run_log.py. RUN_LOG_PATH di-monkeypatch ke tmp_path -- TIDAK PERNAH
menulis ke workspace/state/run_log.jsonl yang asli."""

import json

import run_log


def test_log_event_menulis_baris_json_valid(tmp_path, monkeypatch):
    log_path = tmp_path / "run_log.jsonl"
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(log_path))

    run_log.log_event("run_started", "abc123", chat_id="531508359", extra_field="x")

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "run_started"
    assert entry["run_id"] == "abc123"
    assert entry["chat_id"] == "531508359"
    assert entry["extra_field"] == "x"
    assert "ts" in entry


def test_log_event_append_multi_baris(tmp_path, monkeypatch):
    log_path = tmp_path / "run_log.jsonl"
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(log_path))

    run_log.log_event("run_started", "run-1")
    run_log.log_event("run_finished", "run-1", status="SUCCESS")

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "run_started"
    assert json.loads(lines[1])["event"] == "run_finished"
    assert json.loads(lines[1])["status"] == "SUCCESS"


def test_log_event_gagal_tulis_tidak_melempar_exception(tmp_path, monkeypatch, capsys):
    # arahkan ke path yang mustahil ditulis (direktori sbg "file")
    bad_path = tmp_path / "bukan_file"
    bad_path.mkdir()
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(bad_path))  # ini direktori, bukan file

    run_log.log_event("run_started", "run-x")  # TIDAK BOLEH melempar

    assert "[warn]" in capsys.readouterr().out
