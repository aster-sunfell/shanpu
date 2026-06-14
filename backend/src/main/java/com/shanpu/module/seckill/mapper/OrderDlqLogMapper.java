package com.shanpu.module.seckill.mapper;

import com.shanpu.module.seckill.domain.entity.OrderDlqLog;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface OrderDlqLogMapper {

    int upsert(@Param("orderId") String orderId,
               @Param("payload") String payload,
               @Param("failReason") String failReason);

    List<OrderDlqLog> selectPending(@Param("limit") int limit);

    int markRepublished(@Param("id") Long id);

    int incrementFailure(@Param("id") Long id,
                         @Param("failReason") String failReason);

    int markError(@Param("id") Long id,
                  @Param("failReason") String failReason,
                  @Param("failCount") int failCount);

    long countByStatus(@Param("status") int status);
}
