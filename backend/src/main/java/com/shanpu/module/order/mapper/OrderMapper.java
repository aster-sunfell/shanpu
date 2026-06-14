package com.shanpu.module.order.mapper;

import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.domain.vo.OrderDetailVO;
import com.shanpu.module.order.domain.vo.OrderItemVO;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;
import java.util.List;

public interface OrderMapper {

    int insert(Order order);

    int insertIgnore(Order order);

    int existsByOrderId(@Param("orderId") String orderId);

    int countByUserActivity(@Param("userId") Long userId, @Param("activityId") Long activityId);

    long countPaidByActivity(@Param("activityId") Long activityId);

    List<Order> selectTimeoutPending(@Param("timeoutBefore") LocalDateTime timeoutBefore,
                                      @Param("limit") int limit);

    int cancelPendingByOrderId(@Param("orderId") String orderId);

    List<Order> selectPaidOrdersMissingPointLog(@Param("limit") int limit);

    List<OrderItemVO> selectPageByUser(@Param("userId") Long userId,
                                       @Param("status") Integer status,
                                       @Param("offset") int offset,
                                       @Param("limit") int limit);

    long countByUser(@Param("userId") Long userId, @Param("status") Integer status);

    OrderDetailVO selectByOrderId(@Param("userId") Long userId, @Param("orderId") String orderId);
}
