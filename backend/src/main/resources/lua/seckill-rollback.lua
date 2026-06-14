-- KEYS[1] = shanpu:seckill:stock:{activityId}
-- KEYS[2] = shanpu:seckill:bought:{activityId}
-- ARGV[1] = userId

local removed = redis.call('SREM', KEYS[2], ARGV[1])
if removed == 1 then
    redis.call('INCR', KEYS[1])
end
return removed
