package com.shanpu.module.product.service;

import com.shanpu.common.result.PageResult;
import com.shanpu.module.product.domain.vo.ProductDetailVO;
import com.shanpu.module.product.domain.vo.ProductVO;

public interface ProductService {

    PageResult<ProductVO> listOnSale(int page, int size);

    ProductDetailVO getDetail(Long id);
}
