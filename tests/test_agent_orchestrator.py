import sys
import os
import importlib.util

import pytest
from fastapi.testclient import TestClient

AGENT_ORCHESTRATOR_SRC = os.path.join(
    os.path.dirname(__file__), "..", "services", "agent-orchestrator", "src"
)
AGENT_ORCHESTRATOR_SRC = os.path.abspath(AGENT_ORCHESTRATOR_SRC)

if AGENT_ORCHESTRATOR_SRC not in sys.path:
    sys.path.insert(0, AGENT_ORCHESTRATOR_SRC)

src_pkg = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(
        "src",
        os.path.join(AGENT_ORCHESTRATOR_SRC, "__init__.py"),
    )
)
sys.modules["src"] = src_pkg

spec = importlib.util.spec_from_file_location(
    "src.main",
    os.path.join(AGENT_ORCHESTRATOR_SRC, "main.py"),
)
main_mod = importlib.util.module_from_spec(spec)
sys.modules["src.main"] = main_mod
spec.loader.exec_module(main_mod)

app = main_mod.app
client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_agent_message_returns_503_when_graph_not_ready():
    resp = client.post("/agent/message", json={"agent_id": "a1", "message": "hi"})
    assert resp.status_code == 503
