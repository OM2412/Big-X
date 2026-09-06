import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .dispatcher import Notification, NotificationChannel, NotificationDispatcher, NotificationType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Notification Service")

notification_dispatcher = NotificationDispatcher(email_channel=None, discord_channel=None, telegram_channel=None, sms_channel=None, db_session_factory=lambda: None)


class NotificationRequest(BaseModel):
    user_id: str
    type: str
    subject: str
    body: str


class NotificationResponse(BaseModel):
    status: str
    results: list


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "notification-service"}


@app.get("/ready")
async def readiness() -> dict:
    return {"status": "ready", "service": "notification-service"}


@app.post("/notifications/send", response_model=NotificationResponse)
async def send_notification(payload: NotificationRequest) -> NotificationResponse:
    try:
        notification = Notification(
            type=NotificationType(payload.type),
            recipient_user_id=payload.user_id,
            subject=payload.subject,
            body=payload.body,
        )
        results = await notification_dispatcher.dispatch(notification)
        return NotificationResponse(status="sent", results=results)
    except Exception as exc:
        logger.exception("Notification dispatch failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
