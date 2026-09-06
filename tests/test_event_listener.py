import sys
import os
import importlib.util
from unittest import mock

from pathlib import Path
import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
TESTED_SERVICE = "event-listener"
SRC_PATH = ROOT / "services" / TESTED_SERVICE / "src"
SRC_PATH = str(SRC_PATH)

if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

src_pkg = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(
        "src",
        os.path.join(SRC_PATH, "__init__.py"),
    )
)
sys.modules["src"] = src_pkg

listeners_pkg = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(
        "src.listeners",
        os.path.join(SRC_PATH, "listeners", "__init__.py"),
    )
)
sys.modules["src.listeners"] = listeners_pkg

listener_manager_mock = mock.MagicMock()
listener_manager_mock.start = mock.AsyncMock(return_value=None)

with mock.patch.dict(
    sys.modules,
    {
        "src.listeners.index": mock.MagicMock(),
    },
):
    spec = importlib.util.spec_from_file_location(
        "src.main",
        os.path.join(SRC_PATH, "main.py"),
    )
    main_mod = importlib.util.module_from_spec(spec)
    sys.modules["src.main"] = main_mod
    spec.loader.exec_module(main_mod)

# Patch listener_manager with awaitable mock
main_mod.listener_manager = listener_manager_mock

app = main_mod.app
client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readiness():
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_listen_endpoint():
    resp = client.post("/events/listen")
    assert resp.status_code in (200, 503)
