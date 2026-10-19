-- 秒杀预扣减回滚脚本，适用于限时档同步落库失败和超级档 Kafka 提交失败。
--
-- KEYS[1] = shanpu:seckill:stock:{activityId}
-- KEYS[2] = shanpu:seckill:bought:{activityId}
-- ARGV[1] = userId

-- SREM 是幂等门：只有确实移除了用户已购标记，才允许回补库存。
local removed = redis.call('SREM', KEYS[2], ARGV[1])
if removed == 1 then
    redis.call('INCR', KEYS[1])
end

-- 返回 1 表示本次实际回滚，返回 0 表示此前已经回滚或用户没有预扣成功。
return removed
