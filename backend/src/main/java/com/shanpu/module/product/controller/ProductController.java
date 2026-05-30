package com.shanpu.module.product.controller;

import com.shanpu.common.result.PageResult;
import com.shanpu.common.result.Result;
import com.shanpu.common.util.UserContext;
import com.shanpu.module.order.domain.vo.OrderVO;
import com.shanpu.module.order.service.OrderService;
import com.shanpu.module.product.domain.vo.ProductDetailVO;
import com.shanpu.module.product.domain.vo.ProductVO;
import com.shanpu.module.product.service.ProductService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/products")
@RequiredArgsConstructor
public class ProductController {

    private final ProductService productService;
    private final OrderService orderService;

    @GetMapping
    public Result<PageResult<ProductVO>> list(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int size) {
        return Result.ok(productService.listOnSale(page, size));
    }

    @GetMapping("/{id}")
    public Result<ProductDetailVO> detail(@PathVariable Long id) {
        return Result.ok(productService.getDetail(id));
    }

    // TODO M3: @RateLimit(window=1, max=10, dim=USER)
    @PostMapping("/{id}/order")
    public Result<OrderVO> order(@PathVariable Long id) {
        return Result.ok(orderService.createNormalOrder(UserContext.requireUserId(), id, 1));
    }
}
