package com.shanpu.module.seckill.mapper;

import org.apache.ibatis.annotations.Param;

public interface OrderDlqLogMapper {

    int upsert(@Param("orderId") String orderId, @Param("failReason") String failReason);
}
