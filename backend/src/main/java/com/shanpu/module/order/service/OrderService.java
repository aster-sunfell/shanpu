package com.shanpu.module.order.service;

import com.shanpu.common.result.PageResult;
import com.shanpu.module.order.domain.vo.OrderDetailVO;
import com.shanpu.module.order.domain.vo.OrderItemVO;
import com.shanpu.module.order.domain.vo.OrderVO;

public interface OrderService {

    OrderVO createNormalOrder(Long userId, Long productId, int quantity);

    PageResult<OrderItemVO> getMyOrders(Long userId, String status, int page, int size);

    OrderDetailVO getOrderDetail(Long userId, String orderId);
}
