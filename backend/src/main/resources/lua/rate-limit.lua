-- KEYS[1] = 滑动窗口限流 Key
-- ARGV[1] = 当前毫秒时间戳
-- ARGV[2] = 窗口长度，单位秒
-- ARGV[3] = 窗口内最大请求数
-- ARGV[4] = 本次请求在 ZSet 中的唯一成员值
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2]) * 1000
local max = tonumber(ARGV[3])
local member = ARGV[4]

-- 删除窗口外的历史请求，只保留当前滑动窗口内的访问点。
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
-- 统计窗口内剩余请求数，达到阈值时拒绝本次请求。
local count = redis.call('ZCARD', key)
if count >= max then
    -- 被拒绝时也刷新 TTL，让限流 Key 在窗口结束后自动释放。
    redis.call('PEXPIRE', key, window)
    return 0
end

-- 放行请求时写入本次访问点，score 使用毫秒时间戳参与下一次窗口统计。
redis.call('ZADD', key, now, member)
-- 每次访问后刷新 TTL，避免低频访问留下无过期的 ZSet。
redis.call('PEXPIRE', key, window)
return 1
