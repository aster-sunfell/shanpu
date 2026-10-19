package com.shanpu.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.serializer.StringRedisSerializer;
import org.springframework.scripting.support.ResourceScriptSource;
import org.springframework.core.io.ClassPathResource;

@Configuration
public class RedisConfig {

    /**
     * 创建仅使用字符串序列化的 RedisTemplate。
     *
     * <p>秒杀库存、已购集合、限流窗口等 Redis Key 都以字符串读写，
     * 统一序列化方式保证 Lua 脚本读取到可比较的明文字符串。</p>
     *
     * @param connectionFactory Redis 连接工厂
     * @return 字符串 RedisTemplate
     */
    @Bean
    public StringRedisTemplate stringRedisTemplate(RedisConnectionFactory connectionFactory) {
        StringRedisTemplate template = new StringRedisTemplate(connectionFactory);
        StringRedisSerializer serializer = new StringRedisSerializer();
        template.setKeySerializer(serializer);
        template.setValueSerializer(serializer);
        template.setHashKeySerializer(serializer);
        template.setHashValueSerializer(serializer);
        template.afterPropertiesSet();
        return template;
    }

    /**
     * 加载滑动窗口限流 Lua 脚本。
     *
     * @return 返回 Long 结果的 Redis 脚本对象
     */
    @Bean
    public DefaultRedisScript<Long> rateLimitScript() {
        DefaultRedisScript<Long> script = new DefaultRedisScript<>();
        script.setResultType(Long.class);
        script.setScriptSource(new ResourceScriptSource(new ClassPathResource("lua/rate-limit.lua")));
        return script;
    }

    /**
     * 加载秒杀预扣减 Lua 脚本。
     *
     * <p>该脚本一次性完成库存存在性检查、库存扣减和已购集合写入，
     * 是限时档与超级档防超卖的一致入口。</p>
     *
     * @return 返回 Long 业务码的 Redis 脚本对象
     */
    @Bean
    public DefaultRedisScript<Long> seckillScript() {
        DefaultRedisScript<Long> script = new DefaultRedisScript<>();
        script.setResultType(Long.class);
        script.setScriptSource(new ResourceScriptSource(new ClassPathResource("lua/seckill.lua")));
        return script;
    }

    /**
     * 加载秒杀预扣减回滚 Lua 脚本。
     *
     * <p>同步落库或 Kafka 提交失败时调用该脚本，撤销 Redis 已购标记并幂等回补库存。</p>
     *
     * @return 返回 Long 结果的 Redis 脚本对象
     */
    @Bean
    public DefaultRedisScript<Long> seckillRollbackScript() {
        DefaultRedisScript<Long> script = new DefaultRedisScript<>();
        script.setResultType(Long.class);
        script.setScriptSource(new ResourceScriptSource(new ClassPathResource("lua/seckill-rollback.lua")));
        return script;
    }
}
