import functools

import redis

from .config import settings


@functools.lru_cache(maxsize=1)
def redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
