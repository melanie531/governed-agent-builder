"""Exercise AgentCore's real ASGI health boundary during active invocations."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event

import pytest
from starlette.testclient import TestClient

from runtime.journey import main


@pytest.mark.parametrize("fail", [False, True])
def test_active_invocation_reports_busy_and_releases_health_on_every_exit(monkeypatch, fail):
    entered, finish = Event(), Event()

    def blocked_invocation(payload, context):
        entered.set()
        if not finish.wait(5):
            raise TimeoutError("Controlled invocation did not finish")
        if fail:
            raise ValueError("Controlled provider failure")
        return {"status": "SUCCEEDED"}

    monkeypatch.setattr(main, "invoke", blocked_invocation)
    app = main.create_app()
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as workers:
        assert client.get("/ping").json()["status"] == "Healthy"
        future = workers.submit(client.post, "/invocations", json={
            "input": "private question", "user_token": "private-token"})
        try:
            assert entered.wait(3)
            assert client.get("/ping").json()["status"] == "HealthyBusy"
            tasks = json.dumps(app.get_async_task_info())
            assert "private-token" not in tasks and "private question" not in tasks
        finally:
            finish.set()
        response = future.result(timeout=5)
        assert response.status_code == (500 if fail else 200)
        assert client.get("/ping").json()["status"] == "Healthy"
        assert app.get_async_task_info()["active_count"] == 0


def test_finishing_one_invocation_does_not_mark_another_active_invocation_idle(monkeypatch):
    entered = {name: Event() for name in ("first", "second")}
    finish = {name: Event() for name in entered}

    def blocked_invocation(payload, context):
        name = payload["name"]
        entered[name].set()
        if not finish[name].wait(5):
            raise TimeoutError("Controlled invocation did not finish")
        return {"status": "SUCCEEDED"}

    monkeypatch.setattr(main, "invoke", blocked_invocation)
    app = main.create_app()
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as workers:
        futures = {name: workers.submit(client.post, "/invocations", json={"name": name})
                   for name in entered}
        try:
            assert all(event.wait(3) for event in entered.values())
            assert client.get("/ping").json()["status"] == "HealthyBusy"
            finish["first"].set()
            assert futures["first"].result(timeout=5).status_code == 200
            assert client.get("/ping").json()["status"] == "HealthyBusy"
            assert app.get_async_task_info()["active_count"] == 1
        finally:
            for event in finish.values():
                event.set()
        assert futures["second"].result(timeout=5).status_code == 200
        assert client.get("/ping").json()["status"] == "Healthy"
        assert app.get_async_task_info()["active_count"] == 0
