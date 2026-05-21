from redis.asyncio import Redis, from_url


def create_redis_client(url: str) -> Redis:
    return from_url(url, encoding="utf-8", decode_responses=True)


async def ping_redis(redis_client: Redis) -> None:
    result = await redis_client.ping()
    if result is not True:
        raise RuntimeError("Redis ping returned a non-successful result")


async def close_redis(redis_client: Redis) -> None:
    await redis_client.aclose()
