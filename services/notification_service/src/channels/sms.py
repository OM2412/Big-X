import logging
import re

import httpx

try:
    from .base import NotificationChannel, PermanentFailure, TransientFailure, MetricsHook
except ImportError:
    from base import NotificationChannel, PermanentFailure, TransientFailure, MetricsHook

logger = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
E164_PATTERN = re.compile(r"^\+[1-9]\d{1,14}$")  # E.164 — Twilio requires this format
SMS_LENGTH_WARN_THRESHOLD = 160  # single-segment limit — beyond this Twilio splits into multiple billed segments


class SmsChannel(NotificationChannel):
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        metrics: MetricsHook | None = None,
        max_connections: int = 50,
    ):
        super().__init__(metrics=metrics)
        self._account_sid = account_sid
        self._auth_token = auth_token  # never logged
        self.from_number = from_number

        # Bounded connection pool — under load (thousands of notifications
        # fanning out concurrently) an unbounded client can exhaust file
        # descriptors; a shared, limited pool keeps this predictable.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=20),
            auth=(account_sid, auth_token),
        )

    async def send(self, recipient: str, subject: str, body: str) -> None:
        if not E164_PATTERN.match(recipient):
            raise PermanentFailure(f"Invalid phone number format (must be E.164): {self._redact(recipient)}")

        # SMS has no subject line — fold it into the body if present.
        message = f"{subject}: {body}" if subject else body
        if len(message) > SMS_LENGTH_WARN_THRESHOLD:
            logger.info("SMS body exceeds %d chars — will be billed as multiple segments", SMS_LENGTH_WARN_THRESHOLD)

        url = f"{TWILIO_API_BASE}/Accounts/{self._account_sid}/Messages.json"
        payload = {"To": recipient, "From": self.from_number, "Body": message}

        try:
            response = await self._http.post(url, data=payload)
        except httpx.TimeoutException as exc:
            raise TransientFailure(f"Twilio request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise TransientFailure(f"Could not connect to Twilio: {exc}") from exc

        self._handle_response(response, recipient)

    def _handle_response(self, response: httpx.Response, recipient: str) -> None:
        if response.status_code in (200, 201):
            body = response.json()
            logger.info("SMS sent to %s, sid=%s, status=%s", self._redact(recipient), body.get("sid"), body.get("status"))
            return

        if response.status_code == 429:
            raise TransientFailure("Twilio rate limit hit")

        # Twilio's own error codes distinguish permanent from transient
        # better than HTTP status alone — 21211 (invalid number), 21610
        # (unsubscribed recipient) will never succeed on retry.
        try:
            error_code = response.json().get("code")
        except Exception:
            error_code = None

        permanent_error_codes = {21211, 21610, 21614, 21408}  # invalid number, opted out, unreachable, geo-permission
        if error_code in permanent_error_codes:
            raise PermanentFailure(f"Twilio rejected message permanently: code={error_code}")

        if response.status_code in (400, 401, 403):
            raise PermanentFailure(f"Twilio rejected request: {response.status_code} {response.text[:200]}")

        if response.status_code >= 500:
            raise TransientFailure(f"Twilio server error: {response.status_code}")

        raise PermanentFailure(f"Unexpected Twilio response: {response.status_code} {response.text[:200]}")

    def _redact(self, phone_number: str) -> str:
        return f"{phone_number[:4]}***{phone_number[-2:]}" if len(phone_number) > 6 else "***"

    async def close(self):
        await self._http.aclose()