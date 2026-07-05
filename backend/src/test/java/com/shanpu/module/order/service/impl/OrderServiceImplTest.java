package com.shanpu.module.order.service.impl;

import cn.hutool.core.lang.Snowflake;
import com.fasterxml.jackson.databind.JavaType;
import com.shanpu.cache.CacheClient;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.domain.vo.OrderDetailVO;
import com.shanpu.module.order.domain.vo.OrderVO;
import com.shanpu.module.order.mapper.OrderMapper;
import com.shanpu.module.product.domain.entity.Product;
import com.shanpu.module.product.mapper.ProductMapper;
import com.shanpu.module.user.domain.entity.PointLog;
import com.shanpu.module.user.mapper.PointLogMapper;
import com.shanpu.module.user.mapper.UserMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Duration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderServiceImplTest {

    @Mock
    private ProductMapper productMapper;

    @Mock
    private UserMapper userMapper;

    @Mock
    private OrderMapper orderMapper;

    @Mock
    private PointLogMapper pointLogMapper;

    @Mock
    private Snowflake snowflake;

    @Mock
    private CacheClient cacheClient;

    @InjectMocks
    private OrderServiceImpl orderService;

    private Product onSaleProduct(int price, int stock) {
        Product p = new Product();
        p.setId(5L);
        p.setTitle("限量联名马克杯");
        p.setPrice(price);
        p.setStock(stock);
        p.setStatus(Product.STATUS_ON);
        return p;
    }

    @Test
    void createNormalOrder_stockShort_throwsAndSkipsPointsAndOrder() {
        when(productMapper.selectById(5L)).thenReturn(onSaleProduct(300, 0));
        when(productMapper.deductStock(5L, 1)).thenReturn(0);

        BusinessException ex = assertThrows(BusinessException.class,
                () -> orderService.createNormalOrder(1L, 5L, 1));
        assertEquals(ResultCode.STOCK_SHORTAGE, ex.getResultCode());

        verify(userMapper, never()).deductPoints(anyLong(), anyInt());
        verify(orderMapper, never()).insert(any());
        verify(pointLogMapper, never()).insert(any());
    }

    @Test
    void createNormalOrder_pointsShort_throwsAndSkipsOrder() {
        when(productMapper.selectById(5L)).thenReturn(onSaleProduct(300, 10));
        when(productMapper.deductStock(5L, 1)).thenReturn(1);
        when(userMapper.deductPoints(1L, 300)).thenReturn(0);

        BusinessException ex = assertThrows(BusinessException.class,
                () -> orderService.createNormalOrder(1L, 5L, 1));
        assertEquals(ResultCode.POINTS_NOT_ENOUGH, ex.getResultCode());

        verify(orderMapper, never()).insert(any());
        verify(pointLogMapper, never()).insert(any());
    }

    @Test
    void createNormalOrder_happyPath_writesOrderAndLog() {
        when(productMapper.selectById(5L)).thenReturn(onSaleProduct(300, 10));
        when(productMapper.deductStock(5L, 1)).thenReturn(1);
        when(userMapper.deductPoints(1L, 300)).thenReturn(1);
        when(snowflake.nextIdStr()).thenReturn("9000000000001");
        when(userMapper.selectPointsById(1L)).thenReturn(9700);

        OrderVO vo = orderService.createNormalOrder(1L, 5L, 1);

        assertEquals("9000000000001", vo.getOrderId());
        assertEquals("PAID", vo.getStatus());

        ArgumentCaptor<Order> orderCaptor = ArgumentCaptor.forClass(Order.class);
        verify(orderMapper).insert(orderCaptor.capture());
        Order order = orderCaptor.getValue();
        assertEquals("9000000000001", order.getOrderId());
        assertEquals(1L, order.getUserId());
        assertEquals(5L, order.getProductId());
        assertNull(order.getActivityId());
        assertEquals(Order.SOURCE_NORMAL, order.getSource());
        assertEquals(300, order.getPrice());
        assertEquals(Order.STATUS_PAID, order.getStatus());
        assertNotNull(order.getPaidAt());

        ArgumentCaptor<PointLog> logCaptor = ArgumentCaptor.forClass(PointLog.class);
        verify(pointLogMapper).insert(logCaptor.capture());
        PointLog log = logCaptor.getValue();
        assertEquals(-300, log.getDelta());
        assertEquals(9700, log.getBalanceAfter());
        assertEquals(PointLog.BIZ_ORDER_CONSUME, log.getBizType());
        assertEquals("9000000000001", log.getBizRef());

        verify(cacheClient).delete(RedisKeys.productDetail(5L));
        verify(cacheClient).delete(RedisKeys.productDetailNull(5L));
        verify(cacheClient).deleteByPattern(RedisKeys.productListPattern());
    }

    @Test
    void createNormalOrder_cacheInvalidationFails_keepsOrderResponse() {
        when(productMapper.selectById(5L)).thenReturn(onSaleProduct(300, 10));
        when(productMapper.deductStock(5L, 1)).thenReturn(1);
        when(userMapper.deductPoints(1L, 300)).thenReturn(1);
        when(snowflake.nextIdStr()).thenReturn("9000000000001");
        when(userMapper.selectPointsById(1L)).thenReturn(9700);
        doThrow(new IllegalStateException("redis down"))
                .when(cacheClient).delete(RedisKeys.productDetail(5L));

        OrderVO vo = orderService.createNormalOrder(1L, 5L, 1);

        assertEquals("9000000000001", vo.getOrderId());
        assertEquals("PAID", vo.getStatus());
        verify(orderMapper).insert(any(Order.class));
        verify(pointLogMapper).insert(any(PointLog.class));
        verify(cacheClient).delete(RedisKeys.productDetailNull(5L));
        verify(cacheClient).deleteByPattern(RedisKeys.productListPattern());
    }

    @Test
    void createNormalOrder_productNotFound_throws() {
        when(productMapper.selectById(999L)).thenReturn(null);

        BusinessException ex = assertThrows(BusinessException.class,
                () -> orderService.createNormalOrder(1L, 999L, 1));
        assertEquals(ResultCode.NOT_FOUND, ex.getResultCode());

        verify(productMapper, never()).deductStock(anyLong(), anyInt());
    }

    @Test
    void getOrderDetail_cacheMiss_usesNoNullPlaceholder() {
        JavaType type = mock(JavaType.class);
        when(cacheClient.javaType(OrderDetailVO.class)).thenReturn(type);

        BusinessException ex = assertThrows(BusinessException.class,
                () -> orderService.getOrderDetail(7L, "9000000000003"));

        assertEquals(ResultCode.NOT_FOUND, ex.getResultCode());
        verify(cacheClient).queryWithMutex(
                eq(RedisKeys.orderDetail(7L, "9000000000003")),
                eq(type),
                any(),
                eq(RedisKeys.orderDetailLock(7L, "9000000000003")),
                eq(Duration.ofSeconds(30))
        );
    }
}
