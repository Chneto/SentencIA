# -*- coding: utf-8 -*-
"""Testes do teardown resiliente do servidor E2E no Windows (TASK-025)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.e2e.conftest import _LocalAppServer


class _FakeProc:
    def __init__(self, *, timeout_on_first_wait: bool = False) -> None:
        self.pid = 999999
        self._timeout_on_first_wait = timeout_on_first_wait
        self.eventos: list[str] = []
        self._wait_calls = 0

    def poll(self):
        return None if "killed" not in self.eventos else 0

    def terminate(self) -> None:
        self.eventos.append("terminate")

    def kill(self) -> None:
        self.eventos.append("kill")

    def wait(self, timeout=None) -> int:
        self._wait_calls += 1
        self.eventos.append(f"wait:{self._wait_calls}")
        if self._timeout_on_first_wait and self._wait_calls == 1:
            raise subprocess.TimeoutExpired(cmd=["uvicorn"], timeout=timeout or 5)
        self.eventos.append("killed")
        return 0


def test_local_app_server_chama_wait_apos_kill_e_taskkill_windows(monkeypatch: pytest.MonkeyPatch):
    srv = _LocalAppServer()
    fake_proc = _FakeProc(timeout_on_first_wait=True)
    srv._proc = fake_proc  # type: ignore[assignment]

    comandos_run: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        comandos_run.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    srv.__exit__(None, None, None)

    assert "terminate" in fake_proc.eventos
    assert "kill" in fake_proc.eventos
    assert "wait:2" in fake_proc.eventos
    assert fake_proc.eventos.index("kill") < fake_proc.eventos.index("wait:2")
    if sys.platform == "win32":
        assert any("taskkill" in c[0].lower() and "/T" in c and "/F" in c for c in comandos_run)


def test_local_app_server_cleanup_retenta_e_nao_propaga_permission_error(monkeypatch: pytest.MonkeyPatch):
    srv = _LocalAppServer()
    srv._proc = _FakeProc(timeout_on_first_wait=False)  # type: ignore[assignment]

    tentativas = {"n": 0}

    def _always_locked(path, *args, **kwargs):
        tentativas["n"] += 1
        raise PermissionError(32, "O arquivo já está sendo usado por outro processo", str(path))

    monkeypatch.setattr("tests.e2e.conftest.shutil.rmtree", _always_locked)
    monkeypatch.setattr("tests.e2e.conftest.time.sleep", lambda _s: None)

    # Mesmo com arquivo permanentemente travado, __exit__ tenta várias vezes e NÃO levanta PermissionError.
    srv.__exit__(None, None, None)
    assert tentativas["n"] >= 5
