-- KEYS[1] = shanpu:seckill:stock:{activityId}
-- KEYS[2] = shanpu:seckill:bought:{activityId}
-- ARGV[1] = userId
-- ARGV[2] = orderId

if redis.call('EXISTS', KEYS[1]) == 0 then
    return 3
end

if tonumber(redis.call('GET', KEYS[1])) <= 0 then
    return 1
end

if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
    return 2
end

redis.call('DECR', KEYS[1])
redis.call('SADD', KEYS[2], ARGV[1])
return 0
