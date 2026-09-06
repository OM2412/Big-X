import os
import json
import uuid
import time
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, IncomingMessage, Message
from aio_pika.abc import (
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
    AbstractRobustQueue,
)
from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
MAIN_EXCHANGE = os.getenv("RABBITMQ_MAIN_EXCHANGE", "agentic.events")
RETRY_EXCHANGE = os.getenv("RABBITMQ_RETRY_EXCHANGE", "agentic.retry")
DLX_EXCHANGE = os.getenv("RABBITMQ_DLX_EXCHANGE", "agentic.dlx")
MAX_RETRIES = int(os.getenv("RABBITMQ_MAX_RETRIES", "3"))
RETRY_DELAY_MS = int(os.getenv("RABBITMQ_RETRY_DELAY_MS", "5000"))

# ============================================================
# Observability / Prometheus Metrics
# ============================================================

METRICS_PREFIX = "rabbitmq"

MSG_PUBLISHED = Counter(
    f"{METRICS_PREFIX}_messages_published_total", 
    "Total messages published", 
    ["event_type"]
)
MSG_PROCESSED = Counter(
    f"{METRICS_PREFIX}_messages_processed_total", 
    "Total messages successfully processed", 
    ["event_type", "queue"]
)
MSG_FAILED = Counter(
    f"{METRICS_PREFIX}_messages_failed_total", 
    "Total messages that failed processing", 
    ["event_type", "queue"]
)
MSG_RETRIES = Counter(
    f"{METRICS_PREFIX}_retries_total", 
    "Total messages routed to retry exchange", 
    ["event_type"]
)
MSG_DLQ = Counter(
    f"{METRICS_PREFIX}_dlq_total", 
    "Total messages permanently routed to DLQ", 
    ["event_type", "reason"]
)
PROCESS_DURATION = Histogram(
    f"{METRICS_PREFIX}_processing_duration_seconds", 
    "Time spent processing a message", 
    ["event_type", "queue"]
)

# ============================================================
# Type Aliases
# ============================================================

EventHandler = Callable[[Dict[str, Any]], Awaitable[None]]
IdempotencyChecker = Callable[[str], Awaitable[bool]]
IdempotencyMarker = Callable[[str], Awaitable[None]]

# ============================================================
# RabbitMQ Client
# ============================================================

class RabbitMQClient:
    def __init__(self, rabbitmq_url: str = RABBITMQ_URL):
        self.rabbitmq_url = rabbitmq_url
        self.connection: Optional[AbstractRobustConnection] = None
        self.channel: Optional[AbstractRobustChannel] = None
        self.main_exchange: Optional[AbstractRobustExchange] = None
        self.retry_exchange: Optional[AbstractRobustExchange] = None
        self.dlx_exchange: Optional[AbstractRobustExchange] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    # ========================================================
    # Connection Management
    # ========================================================

    async def connect(self) -> None:
        if self.connection and not self.connection.is_closed:
            return

        logger.info("Connecting to RabbitMQ...")
        self.connection = await aio_pika.connect_robust(self.rabbitmq_url)
        
        # Explicitly enabling publisher confirms for financial-grade assurance
        self.channel = await self.connection.channel(publisher_confirms=True)
        
        await self.channel.set_qos(prefetch_count=10)
        await self._declare_exchanges()
        logger.info("RabbitMQ connection established.")

    async def _declare_exchanges(self) -> None:
        if not self.channel:
            raise RuntimeError("RabbitMQ channel not initialized")

        self.main_exchange = await self.channel.declare_exchange(
            MAIN_EXCHANGE, ExchangeType.TOPIC, durable=True
        )
        self.retry_exchange = await self.channel.declare_exchange(
            RETRY_EXCHANGE, ExchangeType.TOPIC, durable=True
        )
        self.dlx_exchange = await self.channel.declare_exchange(
            DLX_EXCHANGE, ExchangeType.TOPIC, durable=True
        )

    async def get_channel(self) -> AbstractRobustChannel:
        if not self.connection or self.connection.is_closed or not self.channel:
            await self.connect()
        return self.channel

    # ========================================================
    # Queue Setup
    # ========================================================

    async def declare_queue(
        self, queue_name: str, routing_key: str, retry_delay_ms: int = RETRY_DELAY_MS
    ) -> AbstractRobustQueue:
        await self.get_channel()

        # 1. Main Queue (Note: Can optionally add x-dead-letter-exchange here as a fallback)
        queue = await self.channel.declare_queue(queue_name, durable=True)
        await queue.bind(self.main_exchange, routing_key=routing_key)

        # 2. Retry Queue (Routes back to Main Exchange after TTL)
        retry_queue_name = f"{queue_name}.retry"
        retry_queue = await self.channel.declare_queue(
            retry_queue_name,
            durable=True,
            arguments={
                "x-message-ttl": retry_delay_ms,
                "x-dead-letter-exchange": MAIN_EXCHANGE,
                "x-dead-letter-routing-key": routing_key,
            },
        )
        await retry_queue.bind(self.retry_exchange, routing_key=routing_key)

        # 3. Dead Letter Queue
        dlq_name = f"{queue_name}.dlq"
        dlq = await self.channel.declare_queue(dlq_name, durable=True)
        await dlq.bind(self.dlx_exchange, routing_key=routing_key)

        return queue

    # ========================================================
    # Publisher
    # ========================================================

    def create_event(
        self, event_type: str, payload: Dict[str, Any], correlation_id: Optional[str] = None, event_version: str = "1.0"
    ) -> Dict[str, Any]:
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "event_version": event_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }

    async def publish(
        self, routing_key: str, event_type: str, payload: Dict[str, Any], correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Publishes persistent event. Awaits broker confirmation."""
        await self.get_channel()
        event = self.create_event(event_type=event_type, payload=payload, correlation_id=correlation_id)

        message = Message(
            body=json.dumps(event).encode(),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            message_id=event["event_id"],
            correlation_id=event["correlation_id"],
            headers={"event_type": event_type, "retry_count": 0},
        )

        # Because publisher_confirms=True, this awaits an AMQP basic.ack from the broker
        await self.main_exchange.publish(message, routing_key=routing_key)
        
        MSG_PUBLISHED.labels(event_type=event_type).inc()
        logger.info(f"Published (Confirmed) | Event: {event_type} | ID: {event['event_id']}")
        return event

    # ========================================================
    # Consumer & Safe Failure Handling
    # ========================================================

    async def _route_to_dlq_explicitly(self, message: IncomingMessage, routing_key: str, reason: str) -> None:
        """Safely routes poison messages to DLQ without losing them if the publish fails."""
        event_type = (message.headers or {}).get("event_type", "unknown")
        headers = dict(message.headers or {})
        headers["failure_reason"] = reason

        dlq_msg = Message(
            body=message.body,
            content_type=message.content_type or "application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            headers=headers,
        )

        try:
            await self.dlx_exchange.publish(dlq_msg, routing_key=routing_key)
            await message.ack()
            MSG_DLQ.labels(event_type=event_type, reason="poison_message").inc()
            logger.error(f"Poison message safely moved to DLQ | ID: {message.message_id} | Reason: {reason}")
        except Exception:
            logger.exception("CRITICAL: Failed to publish poison message to DLQ. Requeueing to prevent data loss.")
            await message.reject(requeue=True)


    async def _handle_failure(self, message: IncomingMessage, routing_key: str, error: Exception) -> None:
        """Safely manages retries. Will NOT ack original message if retry routing fails."""
        headers = dict(message.headers or {})
        retry_count = int(headers.get("retry_count", 0))
        event_type = headers.get("event_type", "unknown")
        next_retry = retry_count + 1

        headers["retry_count"] = next_retry

        retry_msg = Message(
            body=message.body,
            content_type=message.content_type or "application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            headers=headers,
        )

        try:
            if next_retry <= MAX_RETRIES:
                await self.retry_exchange.publish(retry_msg, routing_key=routing_key)
                MSG_RETRIES.labels(event_type=event_type).inc()
                logger.warning(f"Scheduled for retry ({next_retry}/{MAX_RETRIES}) | ID: {message.message_id}")
            else:
                headers["failure_reason"] = f"Max retries exceeded: {str(error)}"
                retry_msg.headers = headers
                await self.dlx_exchange.publish(retry_msg, routing_key=routing_key)
                MSG_DLQ.labels(event_type=event_type, reason="max_retries").inc()
                logger.error(f"Moved to DLQ (Max retries exceeded) | ID: {message.message_id}")

            # ONLY ack the original message if the publish to Retry/DLQ succeeded
            await message.ack()

        except Exception:
            logger.exception("CRITICAL: Failed to publish to Retry/DLQ exchange. Requeueing original message.")
            # NACK and requeue so the message isn't lost in the void during a network blip
            await message.reject(requeue=True)

    async def consume(
        self,
        queue_name: str,
        routing_key: str,
        handler: EventHandler,
        is_processed: Optional[IdempotencyChecker] = None,
        mark_processed: Optional[IdempotencyMarker] = None,
    ) -> None:
        queue = await self.declare_queue(queue_name, routing_key)

        async def process_message(message: IncomingMessage):
            event_id = message.message_id
            event_type = (message.headers or {}).get("event_type", "unknown")
            start_time = time.perf_counter()

            try:
                # 1. DB-Backed Idempotency Check
                if event_id and is_processed and await is_processed(event_id):
                    logger.info(f"Duplicate ignored (Idempotent) | ID: {event_id}")
                    await message.ack()
                    return

                # 2. Decode Payload
                try:
                    event = json.loads(message.body.decode())
                except json.JSONDecodeError as exc:
                    # Explicit routing for malformed JSON instead of simple reject
                    await self._route_to_dlq_explicitly(message, routing_key, f"Malformed JSON: {exc}")
                    return

                # 3. Execute Operation
                await handler(event)

                # 4. DB-Backed Idempotency Mark & Final ACK
                if event_id and mark_processed:
                    await mark_processed(event_id)
                
                await message.ack()
                
                # 5. Record Success Metrics
                process_time = time.perf_counter() - start_time
                PROCESS_DURATION.labels(event_type=event_type, queue=queue_name).observe(process_time)
                MSG_PROCESSED.labels(event_type=event_type, queue=queue_name).inc()
                
                logger.info(f"Processed successfully in {process_time:.3f}s | ID: {event_id}")

            except Exception as exc:
                MSG_FAILED.labels(event_type=event_type, queue=queue_name).inc()
                logger.exception(f"Processing failed | ID: {event_id}")
                await self._handle_failure(message, routing_key, exc)

        await queue.consume(process_message)
        logger.info(f"Consumer started | Queue: {queue_name} | Routing Key: {routing_key}")

    # ========================================================
    # Lifecycle
    # ========================================================

    async def health_check(self) -> bool:
        try:
            await self.get_channel()
            return bool(self.connection and not self.connection.is_closed and self.channel and not self.channel.is_closed)
        except Exception:
            return False

    async def close(self) -> None:
        if self.connection and not self.connection.is_closed:
            await self.connection.close()
        self.connection = None
        self.channel = None

# ============================================================
# Shared Singleton
# ============================================================

rabbitmq_client = RabbitMQClient()