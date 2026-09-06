import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict

import httpx

logger = logging.getLogger(__name__)

NAMESPACE = os.environ.get("K8S_NAMESPACE", "agentic-defi-platform")

# service_name:port pairs — matches infra/k8s/deployments-and-hpa.yaml's
# containerPort values exactly. Override via HEALTH_CHECK_SERVICES env var
# (comma-separated "name:port") if you add/remove a service, rather than
# editing this file.
DEFAULT_SERVICES = "api-gateway:8000,agent-orchestrator:8001,tool-router:8002,wallet-service:8003,ai-reasoning:8004"


def _parse_services() -> dict[str, str]:
    raw = os.environ.get("HEALTH_CHECK_SERVICES", DEFAULT_SERVICES)
    services = {}
    for entry in raw.split(","):
        name, port = entry.split(":")
        services[name] = f"http://{name}.{NAMESPACE}.svc.cluster.local:{port}/health"
    return services


SERVICES = _parse_services()
TIMEOUT_SECONDS = float(os.environ.get("HEALTH_CHECK_TIMEOUT_SECONDS", "5.0"))


@dataclass
class ServiceHealth:
    name: str
    healthy: bool
    latency_ms: float
    detail: str | None = None


async def check_service(client: httpx.AsyncClient, name: str, url: str) -> ServiceHealth:
    start = time.monotonic()
    try:
        response = await client.get(url, timeout=TIMEOUT_SECONDS)
        latency_ms = (time.monotonic() - start) * 1000
        return ServiceHealth(name=name, healthy=response.status_code == 200, latency_ms=round(latency_ms, 1))
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        return ServiceHealth(name=name, healthy=False, latency_ms=round(latency_ms, 1), detail=str(exc))


async def check_rpc_providers(client: httpx.AsyncClient) -> list[dict]:
    """Surfaces wallet-service's per-chain RPC provider status
    (RpcProviderManager.get_status()) separately — a service can report
    itself healthy while its RPC providers for a specific chain are all
    down, which is exactly what alerting.yaml's AllRpcProvidersDown rule
    watches for, so this endpoint needs to keep existing for that to work."""
    url = f"http://wallet-service.{NAMESPACE}.svc.cluster.local:8003/rpc-status"
    try:
        response = await client.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.error("Could not fetch RPC provider status: %s", exc)
        return []


async def run_health_check() -> tuple[bool, dict]:
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[check_service(client, name, url) for name, url in SERVICES.items()])
        rpc_statuses = await check_rpc_providers(client)

    all_healthy = all(r.healthy for r in results)
    if any(p.get("state") == "down" for p in rpc_statuses):
        all_healthy = False

    report = {
        "timestamp": time.time(),
        "healthy": all_healthy,
        "services": [asdict(r) for r in results],
        "rpc_providers": rpc_statuses,
    }
    return all_healthy, report


def _print_human_readable(report: dict) -> None:
    for service in report["services"]:
        status = "OK" if service["healthy"] else "DOWN"
        detail = f" — {service['detail']}" if service.get("detail") else ""
        print(f"[{status}] {service['name']} ({service['latency_ms']}ms){detail}")

    for provider in report["rpc_providers"]:
        state = provider.get("state", "unknown")
        status = "OK" if state == "healthy" else state.upper()
        print(f"[{status}] rpc-provider:{provider.get('name')} ({provider.get('latency_ms', '?')}ms)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    healthy, report = asyncio.run(run_health_check())

    if os.environ.get("HEALTH_CHECK_OUTPUT") == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_human_readable(report)

    sys.exit(0 if healthy else 1)