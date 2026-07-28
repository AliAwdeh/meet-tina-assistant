from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings


def get_redis_client() -> Redis | None:
    settings = get_settings()
    try:
        return Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)
    except RedisError:
        return None
