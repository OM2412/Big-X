import os, asyncio, logging, aiohttp, redis.asyncio as redis, pybreaker
from typing import Optional, List, Dict, Any
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from prometheus_client import Counter, Histogram
from pydantic import BaseModel

logger = logging.getLogger(__name__)
TG_SENT = Counter('telegram_sent_total', 'Messages sent', ['status'])
TG_LATENCY = Histogram('telegram_latency_seconds', 'Telegram latency')

class TelegramConfig(BaseModel):
    bot_token: str = os.getenv('TELEGRAM_BOT_TOKEN')
    api_url: str = 'https://api.telegram.org/bot'
    max_retries: int = int(os.getenv('TELEGRAM_MAX_RETRIES', 3))
    timeout: int = int(os.getenv('TELEGRAM_TIMEOUT', 30))
    redis_url: str = os.getenv('REDIS_URL', 'redis://localhost:6379')
    rate_limit: int = int(os.getenv('TELEGRAM_RATE_LIMIT', 30))

class NotificationError(Exception): pass
class ValidationError(NotificationError): pass
class ProviderUnavailableError(NotificationError): pass
class RateLimitError(NotificationError): pass

class TelegramClient:
    def __init__(self):
        self.config = TelegramConfig()
        self.session = None
        self.redis = None
        self.breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=300)
        self._queue = []
        
    async def initialize(self):
        """Initialize client"""
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config.timeout))
        self.redis = redis.from_url(self.config.redis_url)
        await self._verify_bot()
        logger.info("Telegram client initialized")
    
    async def _verify_bot(self):
        """Verify bot token"""
        async with self.session.get(f"{self.config.api_url}{self.config.bot_token}/getMe") as resp:
            if resp.status != 200:
                raise ValidationError("Invalid bot token")
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def send_message(self, chat_id: str, text: str, parse_mode: str = 'HTML',
                           disable_preview: bool = False, **kwargs) -> Dict:
        """Send message with rate limiting and circuit breaker"""
        # Rate limiting
        key = f"tg_rate:{chat_id}"
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, 1)
        if count > self.config.rate_limit:
            raise RateLimitError("Rate limit exceeded")
        
        # Validate
        if not chat_id or not text:
            raise ValidationError("Chat ID and text required")
        if len(text) > 4096:
            raise ValidationError("Message too long")
        
        # Sanitize input
        import html
        text = html.escape(text)
        
        # Send with circuit breaker
        @self.breaker
        async def _send():
            url = f"{self.config.api_url}{self.config.bot_token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': text, 'parse_mode': parse_mode,
                      'disable_web_page_preview': disable_preview, **kwargs}
            async with self.session.post(url, json=payload) as resp:
                data = await resp.json()
                if not data.get('ok'):
                    raise ProviderUnavailableError(data.get('description'))
                return data
        
        try:
            result = await _send()
            TG_SENT.labels(status='success').inc()
            return {'status': 'success', 'message_id': result['result']['message_id']}
        except Exception as e:
            TG_SENT.labels(status='failed').inc()
            raise
    
    async def send_markdown(self, chat_id: str, text: str) -> Dict:
        """Send markdown message"""
        return await self.send_message(chat_id, text, parse_mode='MarkdownV2')
    
    async def send_html(self, chat_id: str, text: str) -> Dict:
        """Send HTML message"""
        return await self.send_message(chat_id, text, parse_mode='HTML')
    
    async def send_photo(self, chat_id: str, photo: bytes, caption: str = '') -> Dict:
        """Send photo"""
        url = f"{self.config.api_url}{self.config.bot_token}/sendPhoto"
        data = {'chat_id': chat_id, 'caption': caption}
        files = {'photo': ('photo.jpg', photo, 'image/jpeg')}
        async with self.session.post(url, data=data, files=files) as resp:
            return await resp.json()
    
    async def send_document(self, chat_id: str, document: bytes, filename: str, caption: str = '') -> Dict:
        """Send document"""
        url = f"{self.config.api_url}{self.config.bot_token}/sendDocument"
        data = {'chat_id': chat_id, 'caption': caption}
        files = {'document': (filename, document)}
        async with self.session.post(url, data=data, files=files) as resp:
            return await resp.json()
    
    async def send_transaction_alert(self, chat_id: str, tx: Dict) -> Dict:
        """Format transaction alert"""
        msg = f"<b>💳 Transaction</b>\nHash: <code>{tx.get('hash')}</code>\nAmount: {tx.get('amount')}\nNetwork: {tx.get('network')}"
        return await self.send_html(chat_id, msg)
    
    async def send_security_alert(self, chat_id: str, alert: Dict) -> Dict:
        """Format security alert"""
        emoji = '🔴' if alert.get('severity') == 'critical' else '🟡'
        msg = f"{emoji} <b>Security Alert</b>\n{alert.get('description')}\nTime: {alert.get('timestamp')}"
        return await self.send_html(chat_id, msg)
    
    async def send_bulk_messages(self, messages: List[Dict]) -> Dict:
        """Send multiple messages"""
        tasks = [self.send_message(m['chat_id'], m['text']) for m in messages]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(1 for r in results if not isinstance(r, Exception))
        return {'sent': success, 'total': len(messages)}
    
    async def health_check(self) -> Dict:
        """Health check"""
        try:
            await self._verify_bot()
            await self.redis.ping()
            return {'status': 'healthy', 'breaker': self.breaker.state}
        except Exception as e:
            return {'status': 'unhealthy', 'error': str(e)}
    
    async def close(self):
        """Cleanup"""
        if self.session:
            await self.session.close()
        if self.redis:
            await self.redis.close()