local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2]) * 1000
local max = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= max then
    redis.call('PEXPIRE', key, window)
    return 0
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return 1
