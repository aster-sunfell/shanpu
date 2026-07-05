package com.shanpu.module.product.service.impl;

import com.shanpu.cache.CacheClient;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.product.mapper.ProductMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Duration;
import java.util.function.Supplier;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ProductServiceImplTest {

    @Mock
    private ProductMapper productMapper;

    @Mock
    private CacheClient cacheClient;

    @InjectMocks
    private ProductServiceImpl productService;

    @Test
    @SuppressWarnings("unchecked")
    void getDetail_notFound_usesIndependentNullCacheKey() {
        when(cacheClient.queryWithNullCache(
                eq(RedisKeys.productDetailNull(5L)),
                any(Supplier.class),
                eq(Duration.ofMinutes(2))
        )).thenReturn(null);

        BusinessException ex = assertThrows(BusinessException.class, () -> productService.getDetail(5L));

        assertEquals(ResultCode.NOT_FOUND, ex.getResultCode());
        verify(cacheClient).queryWithNullCache(
                eq(RedisKeys.productDetailNull(5L)),
                any(Supplier.class),
                eq(Duration.ofMinutes(2))
        );
    }
}
