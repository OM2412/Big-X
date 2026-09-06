import sys
import os
import importlib.util
from unittest import mock

from pathlib import Path
import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
TESTED_SERVICE = "guardrail-service"
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

with mock.patch.dict(
    sys.modules,
    {
        "src.abuse_detection": mock.MagicMock(),
        "src.context_sanitization": mock.MagicMock(),
        "src.intent_check": mock.MagicMock(),
        "src.policy_check": mock.MagicMock(),
    },
):
    spec = importlib.util.spec_from_file_location(
        "src.main",
        os.path.join(SRC_PATH, "main.py"),
    )
    main_mod = importlib.util.module_from_spec(spec)
    sys.modules["src.main"] = main_mod
    spec.loader.exec_module(main_mod)

redis_mock = mock.MagicMock()
redis_mock.ping = mock.AsyncMock(return_value=True)
main_mod.redis_client = redis_mock

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


def test_guardrail_request_validation():
    resp = client.post("/guardrail/check", json={})
    assert resp.status_code in (400, 422, 503)
