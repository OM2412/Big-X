import asyncio
import logging
import signal
from fastapi import FastAPI

from .listeners.index import ListenerManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Event Listener")
listener_manager = ListenerManager()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "event-listener"}


@app.get("/ready")
async def readiness() -> dict:
    return {"status": "ready", "service": "event-listener"}


@app.post("/events/listen")
async def listen() -> dict:
    await listener_manager.start()
    return {"status": "started"}
