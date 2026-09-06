import sys
import os
import importlib.util
from unittest import mock

from pathlib import Path
import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
API_GATEWAY_SRC = ROOT / "apps" / "api-gateway" / "src"
API_GATEWAY_SRC = str(API_GATEWAY_SRC)

if API_GATEWAY_SRC not in sys.path:
    sys.path.insert(0, API_GATEWAY_SRC)

# Load real shared modules so error handlers register correctly
shared_api_spec = importlib.util.spec_from_file_location(
    "shared.api.error_handling",
    os.path.join(str(ROOT), "shared", "api", "error_handling.py"),
)
shared_api_pkg = importlib.util.module_from_spec(shared_api_spec)
sys.modules["shared.api.error_handling"] = shared_api_pkg
shared_api_spec.loader.exec_module(shared_api_pkg)

shared_headers_spec = importlib.util.spec_from_file_location(
    "shared.api.headers",
    os.path.join(str(ROOT), "shared", "api", "headers.py"),
)
shared_headers_pkg = importlib.util.module_from_spec(shared_headers_spec)
sys.modules["shared.api.headers"] = shared_headers_pkg
shared_headers_spec.loader.exec_module(shared_headers_pkg)

shared_rate_limit_spec = importlib.util.spec_from_file_location(
    "shared.api.rate_limit",
    os.path.join(str(ROOT), "shared", "api", "rate_limit.py"),
)
shared_rate_limit_pkg = importlib.util.module_from_spec(shared_rate_limit_spec)
sys.modules["shared.api.rate_limit"] = shared_rate_limit_pkg
shared_rate_limit_spec.loader.exec_module(shared_rate_limit_pkg)

# Now load api-gateway package pieces
src_pkg = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(
        "src",
        os.path.join(API_GATEWAY_SRC, "__init__.py"),
    )
)
sys.modules["src"] = src_pkg

routes_spec = importlib.util.spec_from_file_location(
    "src.routes",
    os.path.join(API_GATEWAY_SRC, "routes", "__init__.py"),
)
routes_pkg = importlib.util.module_from_spec(routes_spec)
sys.modules["src.routes"] = routes_pkg
routes_spec.loader.exec_module(routes_pkg)

middleware_spec = importlib.util.spec_from_file_location(
    "src.middleware",
    os.path.join(API_GATEWAY_SRC, "middleware", "__init__.py"),
)
middleware_pkg = importlib.util.module_from_spec(middleware_spec)
sys.modules["src.middleware"] = middleware_pkg
middleware_spec.loader.exec_module(middleware_pkg)


class PassThroughMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)


class PassThroughRateLimiter:
    def __init__(self, app, limit=120, window_seconds=60):
        self.app = app
        self.limit = limit
        self.window_seconds = window_seconds

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


class MockSessionUser:
    user_id = "test-user"
    wallet_address = "0x1234567890123456789012345678901234567890"
    role = "user"


auth_mock = mock.MagicMock()
auth_mock.SessionUser = MockSessionUser
auth_mock.bearer_scheme = mock.MagicMock()


def mock_get_current_user():
    return MockSessionUser()


auth_mock.get_current_user = mock_get_current_user


async def mock_rbac_middleware(request, call_next):
    return await call_next(request)


rbac_mock = mock.MagicMock()
rbac_mock.rbac_middleware = mock_rbac_middleware
integration_mock = mock.MagicMock()
error_handling_mock = mock.MagicMock()
error_handling_mock.register_error_handlers = shared_api_pkg.register_error_handlers
headers_mock = mock.MagicMock()
headers_mock.RequestIDMiddleware = PassThroughMiddleware
rate_limit_mock = mock.MagicMock()
rate_limit_mock.InMemoryRateLimiter = PassThroughRateLimiter

with mock.patch.dict(
    sys.modules,
    {
        "src.middleware.auth": auth_mock,
        "src.middleware.rbac": rbac_mock,
        "src.integration": integration_mock,
        "shared.api.error_handling": error_handling_mock,
        "shared.api.headers": headers_mock,
        "shared.api.rate_limit": rate_limit_mock,
    },
):
    spec = importlib.util.spec_from_file_location(
        "src.main",
        os.path.join(API_GATEWAY_SRC, "main.py"),
    )
    main_mod = importlib.util.module_from_spec(spec)
    sys.modules["src.main"] = main_mod
    spec.loader.exec_module(main_mod)

app = main_mod.app
client = TestClient(app)


def test_gateway_health_and_readiness_endpoints():
    health = client.get("/health")
    readiness = client.get("/ready")

    assert health.status_code == 200
    assert readiness.status_code == 200
    assert health.json()["status"] == "ok"
    assert readiness.json()["status"] == "ready"


def test_gateway_proxy_endpoint_for_tool_router():
    async def fake_proxy(service: str, path: str, payload: dict):
        assert service == "tool-router"
        assert path == "/tool/dispatch"
        return {"status": "dispatched", "result": {"ok": True}}

    token = "fake-token"

    with mock.patch.object(
        main_mod.integration,
        "proxy_request",
        side_effect=fake_proxy,
    ):
        response = client.post(
            "/api/v1/tool/dispatch",
            json={"agent_id": "agent-1", "tool": "swap_tool", "action": "quote", "params": {"src": "ETH", "dst": "USDC", "amount_usd": 100}},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "dispatched"


def test_gateway_versioned_health_route_is_available():
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_gateway_validation_errors_use_consistent_payload():
    token = "fake-token"
    response = client.post(
        "/api/v1/execute",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert "details" in payload["error"]
