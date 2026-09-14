import redis

from app.config import settings

# decode_responses=True: we deal in strings/tokens for locks and counters, not
# raw bytes, and it keeps Slice 3's locking code free of .decode() noise.
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
