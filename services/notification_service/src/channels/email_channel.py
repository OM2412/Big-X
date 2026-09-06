"""
Email Notification Channel - Enterprise-grade with multi-provider failover, templates, attachments, queue, tracing
"""
import os, re, time, json, logging, asyncio, sys
from dataclasses import dataclass, field
_script_dir = os.path.dirname(os.path.abspath(__file__))
_orig_path = sys.path[:]
sys.path = [p for p in sys.path if p not in ("", ".", _script_dir)]
try:
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    from email.mime.image import MIMEImage
    from email.utils import formataddr
finally:
    sys.path = _orig_path
from typing import Optional, List, Dict, Any
from jinja2 import Environment, FileSystemLoader, select_autoescape
import aiosmtplib
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from prometheus_client import Counter, Histogram, Gauge
from opentelemetry import trace
from redis.asyncio import Redis
import hashlib

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)

EMAIL_SENT = Counter("email_sent_total", "Emails sent", ["provider", "status"])
EMAIL_LATENCY = Histogram("email_latency_seconds", "Email latency", ["provider"])
EMAIL_QUEUE = Gauge("email_queue_size", "Email queue size")

@dataclass
class EmailConfig:
    # SMTP
    host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port: int = int(os.getenv("SMTP_PORT", "587"))
    username: str = os.getenv("SMTP_USER", "")
    password: str = os.getenv("SMTP_PASS", "")
    sender: str = os.getenv("FROM_EMAIL", "noreply@example.com")
    sender_name: str = os.getenv("FROM_NAME", "Agentic DeFi")
    timeout: int = 30
    max_retries: int = 3
    rate_limit: int = 100
    # SendGrid
    sendgrid_key: str = os.getenv("SENDGRID_API_KEY", "")
    # AWS SES
    ses_region: str = os.getenv("AWS_REGION", "us-east-1")
    ses_key: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    ses_secret: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    # Queue
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    queue_enabled: bool = os.getenv("EMAIL_QUEUE_ENABLED", "true").lower() == "true"
    # Templates
    template_dir: str = os.getenv("EMAIL_TEMPLATE_DIR", "./templates/email")

class EmailClient:
    def __init__(self, config: Optional[EmailConfig] = None):
        self.cfg = config or EmailConfig()
        self.smtp = None
        self.session = None
        self.redis = None
        self.failures = 0
        self.last_failure = 0
        self.rate_window = time.time()
        self.rate_count = 0
        self.templates = self._init_templates()
        self._init_metrics()
        self._init_tracing()

    def _init_templates(self):
        if os.path.exists(self.cfg.template_dir):
            return Environment(loader=FileSystemLoader(self.cfg.template_dir), autoescape=select_autoescape(["html"]))
        return None

    def _init_metrics(self):
        self.metrics = {"sent": 0, "failed": 0, "retries": 0}

    def _init_tracing(self):
        self.tracer = tracer

    def _valid(self, email: str) -> bool:
        return bool(re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email))

    def _circuit_open(self) -> bool:
        if self.failures >= 5 and time.time() - self.last_failure < 60:
            return True
        if self.failures >= 5 and time.time() - self.last_failure >= 60:
            self.failures = 0
        return False

    def _rate_check(self):
        if time.time() - self.rate_window > 60:
            self.rate_count, self.rate_window = 0, time.time()
        if self.rate_count >= self.cfg.rate_limit:
            raise RuntimeError("Rate limit exceeded")
        self.rate_count += 1

    async def _redis(self):
        if not self.redis and self.cfg.redis_url:
            self.redis = Redis.from_url(self.cfg.redis_url, decode_responses=True)
        return self.redis

    async def _session(self):
        if not self.session:
            self.session = aiohttp.ClientSession()
        return self.session

    async def _connect_smtp(self):
        if self.smtp:
            return
        self.smtp = aiosmtplib.SMTP(self.cfg.host, self.cfg.port, timeout=self.cfg.timeout)
        await self.smtp.connect()
        await self.smtp.starttls()
        await self.smtp.login(self.cfg.username, self.cfg.password)

    async def _render(self, template: str, data: Dict) -> str:
        if self.templates:
            return self.templates.get_template(template).render(**data)
        return data.get("html", "<p>Email content</p>")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30),
           retry=retry_if_exception(lambda e: "timeout" in str(e).lower() or "connection" in str(e).lower()))
    async def _send_smtp(self, to: str, subject: str, html: str, text: str = "", attachments: List[Dict] = None):
        await self._connect_smtp()
        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr((self.cfg.sender_name, self.cfg.sender))
        msg["To"], msg["Subject"] = to, subject
        msg.attach(MIMEText(text or re.sub(r"<[^>]+>", "", html), "plain"))
        msg.attach(MIMEText(html, "html"))
        for a in (attachments or []):
            part = MIMEApplication(a["data"], _subtype=a.get("mime", "octet-stream"))
            part.add_header("Content-Disposition", "attachment", filename=a.get("name", "file"))
            msg.attach(part)
        await self.smtp.send_message(msg)
        return {"status": "sent", "provider": "smtp"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def _send_sendgrid(self, to: str, subject: str, html: str):
        if not self.cfg.sendgrid_key:
            raise RuntimeError("SendGrid key missing")
        session = await self._session()
        url = "https://api.sendgrid.com/v3/mail/send"
        headers = {"Authorization": f"Bearer {self.cfg.sendgrid_key}", "Content-Type": "application/json"}
        data = {"personalizations": [{"to": [{"email": to}]}], "from": {"email": self.cfg.sender},
                "subject": subject, "content": [{"type": "text/html", "value": html}]}
        async with session.post(url, headers=headers, json=data, timeout=self.cfg.timeout) as r:
            if r.status != 202:
                raise RuntimeError(f"SendGrid error: {r.status} {await r.text()}")
        return {"status": "sent", "provider": "sendgrid"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def _send_ses(self, to: str, subject: str, html: str):
        import boto3
        client = boto3.client("ses", region_name=self.cfg.ses_region,
                              aws_access_key_id=self.cfg.ses_key, aws_secret_access_key=self.cfg.ses_secret)
        client.send_email(Source=self.cfg.sender, Destination={"ToAddresses": [to]},
                          Message={"Subject": {"Data": subject}, "Body": {"Html": {"Data": html}}})
        return {"status": "sent", "provider": "ses"}

    async def _audit(self, to: str, subject: str, status: str, provider: str, error: str = ""):
        r = await self._redis()
        if r:
            await r.lpush("email_audit", json.dumps({"to": to, "subject": subject[:50], "status": status,
                "provider": provider, "error": error, "time": time.time()}))

    async def send(self, to: str, subject: str, html: str, text: str = "",
                   template: str = None, data: Dict = None, attachments: List[Dict] = None,
                   bulk: List[str] = None) -> Dict[str, Any]:
        with tracer.start_as_current_span("email.send") as span:
            span.set_attribute("recipient", to[:20])
            if not all(self._valid(t) for t in ([to] + (bulk or []))):
                raise ValueError("Invalid email address")
            if self._circuit_open():
                raise RuntimeError("Circuit breaker open")
            self._rate_check()

            providers = ["smtp", "sendgrid", "ses"]
            if self.cfg.sendgrid_key and "sendgrid" not in providers:
                providers.append("sendgrid")
            if self.cfg.ses_key:
                providers.append("ses")

            # Render template if provided
            if template and data:
                html = await self._render(template, data)
                subject = data.get("subject", subject)

            # Queue if enabled
            if self.cfg.queue_enabled and self.cfg.redis_url:
                r = await self._redis()
                if r:
                    await r.rpush("email_queue", json.dumps({"to": to, "subject": subject, "html": html,
                        "text": text, "attachments": attachments, "bulk": bulk}))
                    EMAIL_QUEUE.inc()
                    return {"status": "queued", "queue": True}

            # Bulk send
            if bulk:
                results = []
                for recipient in bulk:
                    results.append(await self.send(recipient, subject, html, text, attachments=attachments))
                return {"status": "bulk_sent", "count": len(bulk), "results": results}

            # Multi-provider failover
            last_error = None
            for provider in providers:
                try:
                    start = time.time()
                    if provider == "smtp":
                        result = await self._send_smtp(to, subject, html, text, attachments)
                    elif provider == "sendgrid":
                        result = await self._send_sendgrid(to, subject, html)
                    elif provider == "ses":
                        result = await self._send_ses(to, subject, html)
                    else:
                        continue
                    self.failures = 0
                    EMAIL_SENT.labels(provider=provider, status="success").inc()
                    EMAIL_LATENCY.labels(provider=provider).observe(time.time() - start)
                    self.metrics["sent"] += 1
                    await self._audit(to, subject, "sent", provider)
                    return {"status": "sent", "provider": provider, **result}
                except Exception as e:
                    last_error = e
                    logger.warning(f"Provider {provider} failed: {e}")
                    await self._audit(to, subject, "failed", provider, str(e))
                    continue

            self.failures += 1
            self.last_failure = time.time()
            self.metrics["failed"] += 1
            EMAIL_SENT.labels(provider="all", status="failed").inc()
            raise RuntimeError(f"All providers failed: {last_error}")

    async def health_check(self) -> Dict[str, Any]:
        try:
            await self._connect_smtp()
            return {"status": "healthy", "smtp": True, "circuit": self._circuit_open(),
                    "queue": EMAIL_QUEUE._value.get() if hasattr(EMAIL_QUEUE, "_value") else 0,
                    "metrics": self.metrics}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def close(self):
        if self.smtp:
            await self.smtp.quit()
            self.smtp = None
        if self.session:
            await self.session.close()
            self.session = None
        if self.redis:
            await self.redis.close()
            self.redis = None