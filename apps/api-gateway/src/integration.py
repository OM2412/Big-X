import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import aio_pika
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


@dataclass
class ServiceEndpoint:
    name: str
    base_url: str


class GatewayIntegration:
    def __init__(self) -> None:
        self.http_client = httpx.AsyncClient(timeout=5.0)
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
        self.guardrail_url = os.getenv("GUARDRAIL_SERVICE_URL", "http://guardrail-service:8000")
        self.orchestrator_url = os.getenv("AGENT_ORCHESTRATOR_URL", "http://agent-orchestrator:8000")
        self.tool_router_url = os.getenv("TOOL_ROUTER_URL", "http://tool-router:8000")
        self.policy_engine_url = os.getenv("RISK_POLICY_ENGINE_URL", "http://risk-policy-engine:8000")
        self.wallet_url = os.getenv("WALLET_SERVICE_URL", "http://wallet-service:8000")
        self.oracle_url = os.getenv("ORACLE_SERVICE_URL", "http://oracle-service:8000")
        self.notification_url = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8000")
        self.smart_contract_url = os.getenv("SMART_CONTRACT_INTEGRATION_URL", "http://smart-contract-integration:8000")
        self.redis: Redis | None = None
        self.channel = None
        self.connection = None

    async def execute_pipeline(self, agent_id: str, message: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        steps: list[dict[str, Any]] = []

        await self._ensure_redis()
        await self._ensure_rabbitmq()

        guardrail_payload = {"task_id": task_id, "agent_id": agent_id, "message": message, "metadata": metadata or {}}
        guardrail_result = await self._post_json(f"{self.guardrail_url}/guardrail/check", guardrail_payload)
        steps.append({"service": "guardrail", "status": guardrail_result.get("status", "ok")})

        if not guardrail_result.get("allowed", True):
            return {"task_id": task_id, "status": "blocked", "message": guardrail_result.get("reason", "Blocked by guardrail"), "steps": steps}

        orchestrator_payload = {"agent_id": agent_id, "message": message}
        orchestrator_result = await self._post_json(f"{self.orchestrator_url}/agent/message", orchestrator_payload)
        steps.append({"service": "orchestrator", "status": orchestrator_result.get("status", "pending")})

        await self._publish_message("workflow.requests", {"task_id": task_id, "agent_id": agent_id, "message": message, "metadata": metadata or {}})

        return {
            "task_id": task_id,
            "status": orchestrator_result.get("status", "accepted"),
            "message": orchestrator_result.get("reply", "Workflow accepted"),
            "steps": steps,
        }

    async def _ensure_redis(self) -> None:
        if self.redis is None:
            self.redis = Redis.from_url(self.redis_url, decode_responses=True)
            await self.redis.ping()

    async def _ensure_rabbitmq(self) -> None:
        if self.connection is None:
            self.connection = await aio_pika.connect_robust(self.rabbitmq_url)
            self.channel = await self.connection.channel()

    async def _publish_message(self, queue_name: str, payload: dict[str, Any]) -> None:
        if self.channel is None:
            await self._ensure_rabbitmq()
        await self.channel.declare_queue(queue_name, durable=True)
        await self.channel.default_exchange.publish(
            aio_pika.Message(body=json.dumps(payload).encode()),
            routing_key=queue_name,
        )

    async def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.http_client.post(url, json=payload)
        response.raise_for_status()
        return response.json()

    async def proxy_request(self, service: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        service_urls = {
            "guardrail-service": self.guardrail_url,
            "agent-orchestrator": self.orchestrator_url,
            "tool-router": self.tool_router_url,
            "risk-policy-engine": self.policy_engine_url,
            "wallet-service": self.wallet_url,
            "oracle-service": self.oracle_url,
            "notification-service": self.notification_url,
            "smart-contract-integration": self.smart_contract_url,
        }

        base_url = service_urls.get(service)
        if not base_url:
            raise ValueError(f"Unknown service: {service}")

        url = f"{base_url}{path}"
        response = await self.http_client.post(url, json=payload or {})
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        if self.http_client:
            await self.http_client.aclose()
        if self.redis:
            await self.redis.close()
        if self.connection:
            await self.connection.close()
