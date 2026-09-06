import os

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, storage_uri=os.environ.get("REDIS_URL", "redis://redis:6379/0"), default_limits=["200/minute"])
AUTH_RATE_LIMIT = "10/minute"
REFRESH_RATE_LIMIT = "20/minute"
