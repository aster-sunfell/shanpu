package com.shanpu.module.product.mapper;

import com.shanpu.module.product.domain.entity.Product;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface ProductMapper {

    List<Product> selectPage(@Param("offset") int offset, @Param("limit") int limit);

    long countOnSale();

    Product selectById(@Param("id") Long id);

    int deductStock(@Param("id") Long id, @Param("quantity") int quantity);
}
