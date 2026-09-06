import sys
import os
import importlib.util
from unittest import mock

from pathlib import Path
import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
TESTED_SERVICE = "wallet-service"
SRC_PATH = ROOT / "services" / TESTED_SERVICE / "src"
SRC_PATH = str(SRC_PATH)

os.environ.setdefault("CDP_API_KEY_NAME", "test")
os.environ.setdefault("CDP_API_KEY_PRIVATE_KEY", "test")

if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

src_pkg = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(
        "src",
        os.path.join(SRC_PATH, "__init__.py"),
    )
)
sys.modules["src"] = src_pkg

mpc_mock = mock.MagicMock()
spend_mock = mock.MagicMock()

with mock.patch.dict(
    sys.modules,
    {
        "src.mpc_wallet": mpc_mock,
        "src.spend_limits": spend_mock,
    },
):
    spec = importlib.util.spec_from_file_location(
        "src.main",
        os.path.join(SRC_PATH, "main.py"),
    )
    main_mod = importlib.util.module_from_spec(spec)
    sys.modules["src.main"] = main_mod
    spec.loader.exec_module(main_mod)

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


def test_spend_check_validation():
    resp = client.post("/wallet/spend-check", json={})
    assert resp.status_code in (400, 422, 503)
