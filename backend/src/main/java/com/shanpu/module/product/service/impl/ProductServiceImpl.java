package com.shanpu.module.product.service.impl;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.PageResult;
import com.shanpu.common.result.ResultCode;
import com.shanpu.module.product.domain.entity.Product;
import com.shanpu.module.product.domain.vo.ProductDetailVO;
import com.shanpu.module.product.domain.vo.ProductVO;
import com.shanpu.module.product.mapper.ProductMapper;
import com.shanpu.module.product.service.ProductService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class ProductServiceImpl implements ProductService {

    private final ProductMapper productMapper;

    private static final int DEFAULT_SIZE = 20;
    private static final int MAX_SIZE = 50;

    @Override
    public PageResult<ProductVO> listOnSale(int page, int size) {
        int p = Math.max(page, 1);
        int s = size <= 0 ? DEFAULT_SIZE : Math.min(size, MAX_SIZE);
        int offset = (p - 1) * s;
        List<ProductVO> list = productMapper.selectPage(offset, s).stream()
                .map(this::toVO)
                .toList();
        long total = productMapper.countOnSale();
        return PageResult.<ProductVO>builder().list(list).total(total).build();
    }

    @Override
    public ProductDetailVO getDetail(Long id) {
        Product p = productMapper.selectById(id);
        if (p == null) {
            throw new BusinessException(ResultCode.NOT_FOUND);
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
