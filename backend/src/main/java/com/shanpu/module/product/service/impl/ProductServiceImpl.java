package com.shanpu.module.product.service.impl;

import com.fasterxml.jackson.databind.JavaType;
import com.shanpu.cache.CacheClient;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.PageResult;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.product.domain.entity.Product;
import com.shanpu.module.product.domain.vo.ProductDetailVO;
import com.shanpu.module.product.domain.vo.ProductVO;
import com.shanpu.module.product.mapper.ProductMapper;
import com.shanpu.module.product.service.ProductService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class ProductServiceImpl implements ProductService {

    private final ProductMapper productMapper;
    private final CacheClient cacheClient;

    private static final int DEFAULT_SIZE = 20;
    private static final int MAX_SIZE = 50;
    private static final Duration PRODUCT_LIST_TTL = Duration.ofMinutes(5);
    private static final Duration PRODUCT_DETAIL_TTL = Duration.ofMinutes(30);
    private static final Duration PRODUCT_NULL_TTL = Duration.ofMinutes(2);

    @Override
    public PageResult<ProductVO> listOnSale(int page, int size) {
        int p = Math.max(page, 1);
        int s = size <= 0 ? DEFAULT_SIZE : Math.min(size, MAX_SIZE);
        JavaType pageType = cacheClient.javaType(PageResult.class, ProductVO.class);
        return cacheClient.queryWithPassThrough(
                RedisKeys.productList(p, s),
                pageType,
                () -> queryProductPage(p, s),
                PRODUCT_LIST_TTL,
                PRODUCT_NULL_TTL
        );
    }

    @Override
    public ProductDetailVO getDetail(Long id) {
        ProductDetailVO vo = cacheClient.queryWithLogicalExpire(
                RedisKeys.PRODUCT_DETAIL_PREFIX,
                id,
                ProductDetailVO.class,
                this::queryProductDetail,
                PRODUCT_DETAIL_TTL,
                PRODUCT_NULL_TTL
        );
        if (vo == null) {
            throw new BusinessException(ResultCode.NOT_FOUND);
        }
        return vo;
    }

    private PageResult<ProductVO> queryProductPage(int page, int size) {
        int offset = (page - 1) * size;
        List<ProductVO> list = productMapper.selectPage(offset, size).stream()
                .map(this::toVO)
                .toList();
        long total = productMapper.countOnSale();
        return PageResult.<ProductVO>builder().list(list).total(total).build();
    }

    private ProductDetailVO queryProductDetail(Long id) {
        Product p = productMapper.selectById(id);
        if (p == null || p.getStatus() == null || p.getStatus() != Product.STATUS_ON) {
            return null;
        }
        return ProductDetailVO.builder()
                .id(p.getId())
                .title(p.getTitle())
                .coverUrl(p.getCoverUrl())
                .description(p.getDescription())
                .price(p.getPrice())
                .stock(p.getStock())
                .status(p.getStatus())
                .build();
    }

    private ProductVO toVO(Product p) {
        return ProductVO.builder()
                .id(p.getId())
                .title(p.getTitle())
                .coverUrl(p.getCoverUrl())
                .price(p.getPrice())
                .stock(p.getStock())
                .status(p.getStatus())
                .build();
    }
}
