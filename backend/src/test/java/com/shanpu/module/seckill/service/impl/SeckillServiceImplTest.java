package com.shanpu.module.seckill.service.impl;

import cn.hutool.core.lang.Snowflake;
import com.shanpu.cache.CacheClient;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.domain.vo.OrderDetailVO;
import com.shanpu.module.seckill.domain.entity.SeckillActivity;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import com.shanpu.module.seckill.domain.vo.SeckillGrabVO;
import com.shanpu.module.seckill.infra.SeckillProducer;
import com.shanpu.module.seckill.infra.SeckillRedisExecutor;
import com.shanpu.module.seckill.mapper.SeckillActivityMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.function.Supplier;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class SeckillServiceImplTest {

    @Mock
    private SeckillActivityMapper activityMapper;

    @Mock
    private CacheClient cacheClient;

    @Mock
    private SeckillRedisExecutor redisExecutor;

    @Mock
    private SeckillProducer seckillProducer;

    @Mock
    private SeckillOrderWriter orderWriter;

    @Mock
    private Snowflake snowflake;

    @InjectMocks
    private SeckillServiceImpl seckillService;

    @Test
    @SuppressWarnings("unchecked")
    void detail_notFound_usesIndependentNullCacheKey() {
        when(cacheClient.queryWithNullCache(
                eq(RedisKeys.seckillActivityNull(3001L)),
                any(Supplier.class),
                eq(Duration.ofMinutes(2))
        )).thenReturn(null);

        BusinessException ex = assertThrows(BusinessException.class, () -> seckillService.detail(3001L));

        assertEquals(ResultCode.NOT_FOUND, ex.getResultCode());
        verify(cacheClient).queryWithNullCache(
                eq(RedisKeys.seckillActivityNull(3001L)),
                any(Supplier.class),
                eq(Duration.ofMinutes(2))
        );
    }

    @Test
    void grabInclusive_routesToSyncPaidOrder() {
        SeckillActivityVO activity = activity(1001L, SeckillActivity.TIER_INCLUSIVE);
        mockActivityCache(activity);
        when(redisExecutor.currentStock(activity)).thenReturn(4999);
        when(snowflake.nextIdStr()).thenReturn("9000000000001");

        SeckillGrabVO result = seckillService.grab(7L, 1001L);

        assertEquals("9000000000001", result.getOrderId());
        assertEquals("PAID", result.getStatus());
        assertEquals(SeckillActivity.TIER_INCLUSIVE, result.getTier());
        verify(orderWriter).createSyncPaidOrder(
                7L,
                "9000000000001",
                1001L,
                1L,
                1080,
                Order.SOURCE_SECKILL_INCLUSIVE
        );
        verify(redisExecutor, never()).preDeduct(any(), any(), any());
    }

    @Test
    void grabFlash_duplicateFromLua_mapsToDuplicateOrder() {
        SeckillActivityVO activity = activity(2001L, SeckillActivity.TIER_FLASH);
        mockActivityCache(activity);
        when(redisExecutor.currentStock(activity)).thenReturn(100);
        when(snowflake.nextIdStr()).thenReturn("9000000000002");
        when(redisExecutor.preDeduct(2001L, 7L, "9000000000002"))
                .thenReturn(SeckillRedisExecutor.DUPLICATE);

        BusinessException ex = assertThrows(BusinessException.class,
                () -> seckillService.grab(7L, 2001L));

        assertEquals(ResultCode.DUPLICATE_ORDER, ex.getResultCode());
        verify(orderWriter, never()).createSyncPaidOrder(any(), any(), any(), any(), any(Integer.class), any(Integer.class));
    }

    @Test
    void grabSuper_successSendsKafkaAndReturnsPending() {
        SeckillActivityVO activity = activity(3001L, SeckillActivity.TIER_SUPER);
        mockActivityCache(activity);
        when(redisExecutor.currentStock(activity)).thenReturn(99);
        when(snowflake.nextIdStr()).thenReturn("9000000000003");
        when(redisExecutor.preDeduct(3001L, 7L, "9000000000003"))
                .thenReturn(SeckillRedisExecutor.OK);

        SeckillGrabVO result = seckillService.grab(7L, 3001L);

        assertEquals("9000000000003", result.getOrderId());
        assertEquals("PENDING", result.getStatus());
        assertEquals(SeckillActivity.TIER_SUPER, result.getTier());
        ArgumentCaptor<SeckillOrderMsg> msgCaptor = ArgumentCaptor.forClass(SeckillOrderMsg.class);
        verify(seckillProducer).send(msgCaptor.capture());
        assertEquals(Order.SOURCE_SECKILL_SUPER, msgCaptor.getValue().getSource());
        assertEquals(3001L, msgCaptor.getValue().getActivityId());
        assertEquals(7L, msgCaptor.getValue().getUserId());

        ArgumentCaptor<OrderDetailVO> detailCaptor = ArgumentCaptor.forClass(OrderDetailVO.class);
        verify(cacheClient).set(
                eq(RedisKeys.orderDetail(7L, "9000000000003")),
                detailCaptor.capture(),
                eq(Duration.ofSeconds(30))
        );
        OrderDetailVO pending = detailCaptor.getValue();
        assertEquals("9000000000003", pending.getOrderId());
        assertEquals(1L, pending.getProductId());
        assertEquals("机械键盘 87 键", pending.getProductTitle());
        assertEquals(1080, pending.getPrice());
        assertEquals("PENDING", pending.getStatus());
        assertEquals(Order.SOURCE_SECKILL_SUPER, pending.getSource());
    }

    @Test
    void grabSuper_sendFailureRollsBackRedis() {
        SeckillActivityVO activity = activity(3001L, SeckillActivity.TIER_SUPER);
        mockActivityCache(activity);
        when(redisExecutor.currentStock(activity)).thenReturn(99);
        when(snowflake.nextIdStr()).thenReturn("9000000000004");
        when(redisExecutor.preDeduct(3001L, 7L, "9000000000004"))
                .thenReturn(SeckillRedisExecutor.OK);
        doThrow(new BusinessException(ResultCode.INTERNAL_ERROR, "kafka down"))
                .when(seckillProducer).send(any(SeckillOrderMsg.class));

        BusinessException ex = assertThrows(BusinessException.class,
                () -> seckillService.grab(7L, 3001L));

        assertEquals(ResultCode.INTERNAL_ERROR, ex.getResultCode());
        verify(redisExecutor).rollback(3001L, 7L);
        verify(cacheClient, never()).set(
                eq(RedisKeys.orderDetail(7L, "9000000000004")),
                any(OrderDetailVO.class),
                any(Duration.class)
        );
    }

    @Test
    void grabSuper_pendingCacheFailureStillReturnsPending() {
        SeckillActivityVO activity = activity(3001L, SeckillActivity.TIER_SUPER);
        mockActivityCache(activity);
        when(redisExecutor.currentStock(activity)).thenReturn(99);
        when(snowflake.nextIdStr()).thenReturn("9000000000005");
        when(redisExecutor.preDeduct(3001L, 7L, "9000000000005"))
                .thenReturn(SeckillRedisExecutor.OK);
        doThrow(new IllegalStateException("redis down"))
                .when(cacheClient).set(
                        eq(RedisKeys.orderDetail(7L, "9000000000005")),
                        any(OrderDetailVO.class),
                        eq(Duration.ofSeconds(30))
                );

        SeckillGrabVO result = seckillService.grab(7L, 3001L);

        assertEquals("9000000000005", result.getOrderId());
        assertEquals("PENDING", result.getStatus());
        verify(redisExecutor, never()).rollback(3001L, 7L);
    }

    @SuppressWarnings("unchecked")
    private void mockActivityCache(SeckillActivityVO activity) {
        when(cacheClient.queryWithNullCache(
                eq(RedisKeys.seckillActivityNull(activity.getId())),
                any(Supplier.class),
                any(Duration.class)
        )).thenReturn(activity);
    }

    private SeckillActivityVO activity(Long id, int tier) {
        return SeckillActivityVO.builder()
                .id(id)
                .productId(1L)
                .productTitle("机械键盘 87 键")
                .title("秒杀活动")
                .tier(tier)
                .originalPrice(1200)
                .seckillPrice(1080)
                .totalStock(5000)
                .stock(5000)
                .status("ACTIVE")
                .statusCode(SeckillActivity.STATUS_ACTIVE)
                .startAt(LocalDateTime.now().minusMinutes(1))
                .endAt(LocalDateTime.now().plusHours(1))
                .build();
    }
}
