import sys
import os
import importlib.util
from unittest import mock

from pathlib import Path
import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
TESTED_SERVICE = "tool-router"
SRC_PATH = ROOT / "services" / TESTED_SERVICE / "src"
SRC_PATH = str(SRC_PATH)

os.environ.setdefault("RPC_URLS", "http://localhost:8545")

if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

src_pkg = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(
        "src",
        os.path.join(SRC_PATH, "__init__.py"),
    )
)
sys.modules["src"] = src_pkg

tools_pkg = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(
        "src.tools",
        os.path.join(SRC_PATH, "tools", "__init__.py"),
    )
)
sys.modules["src.tools"] = tools_pkg

swap_mock = mock.MagicMock()
bridge_mock = mock.MagicMock()
price_mock = mock.MagicMock()

with mock.patch.dict(
    sys.modules,
    {
        "src.tools.swap_tool": swap_mock,
        "src.tools.bridge_tool": bridge_mock,
        "src.tools.price_feed_tool": price_mock,
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


def test_dispatch_validation():
    resp = client.post("/tool/dispatch", json={})
    assert resp.status_code in (400, 422, 503)
