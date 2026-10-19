-- 秒杀预扣减脚本，适用于限时档和超级档。
--
-- Redis 会以单线程方式原子执行整段脚本，库存校验、一人一单判断、
-- 库存递减和已购集合写入始终作为一个不可分割的执行单元完成。
--
-- KEYS[1] = shanpu:seckill:stock:{activityId}
-- KEYS[2] = shanpu:seckill:bought:{activityId}
-- ARGV[1] = userId
-- ARGV[2] = orderId
--
-- 返回码：
--   0 = 成功预扣库存并写入已购集合
--   1 = 库存不足
--   2 = 用户已购买
--   3 = 活动不存在或库存 Key 尚未预热

-- 库存 Key 不存在时说明活动未完成预热，交给 Java 侧做一次兜底预热后重试。
if redis.call('EXISTS', KEYS[1]) == 0 then
    return 3
end

-- 库存已经扣完时直接拒绝，将无效请求挡在数据库和 Kafka 前。
if tonumber(redis.call('GET', KEYS[1])) <= 0 then
    return 1
end

-- 已购集合是 Redis 层的一人一单前置拦截，数据库唯一索引仍保留兜底。
if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
    return 2
end

-- 先扣库存再记录已购用户；失败补偿由 seckill-rollback.lua 按 userId 幂等撤销。
redis.call('DECR', KEYS[1])
redis.call('SADD', KEYS[2], ARGV[1])
return 0
