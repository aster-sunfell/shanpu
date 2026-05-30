package com.shanpu.module.order.controller;

import com.shanpu.common.result.PageResult;
import com.shanpu.common.result.Result;
import com.shanpu.common.util.UserContext;
import com.shanpu.module.order.domain.vo.OrderDetailVO;
import com.shanpu.module.order.domain.vo.OrderItemVO;
import com.shanpu.module.order.service.OrderService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/orders")
@RequiredArgsConstructor
public class OrderController {

    private final OrderService orderService;

    // TODO M3: @RateLimit(window=1, max=10, dim=USER)
    @GetMapping
    public Result<PageResult<OrderItemVO>> list(
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int size) {
        return Result.ok(orderService.getMyOrders(UserContext.requireUserId(), status, page, size));
    }

    // TODO M3: @RateLimit(window=1, max=10, dim=USER)
    @GetMapping("/{orderId}")
    public Result<OrderDetailVO> detail(@PathVariable String orderId) {
        return Result.ok(orderService.getOrderDetail(UserContext.requireUserId(), orderId));
    }
}
