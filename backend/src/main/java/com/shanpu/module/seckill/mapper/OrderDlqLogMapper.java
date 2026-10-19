package com.shanpu.module.seckill.mapper;

import com.shanpu.module.seckill.domain.entity.OrderDlqLog;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * 超级秒杀订单死信日志 Mapper。
 */
public interface OrderDlqLogMapper {

    /**
     * 写入或更新死信记录。
     *
     * <p>同一订单重复进入 DLQ 时更新失败原因与失败次数，不新增重复记录。</p>
     *
     * @param orderId 订单号
     * @param payload 原始消息 JSON
     * @param failReason 失败原因
     * @return 影响行数
     */
    int upsert(@Param("orderId") String orderId,
               @Param("payload") String payload,
               @Param("failReason") String failReason);

    /**
     * 查询待回投的死信记录。
     *
     * @param limit 查询数量上限
     * @return 待处理记录
     */
    List<OrderDlqLog> selectPending(@Param("limit") int limit);

    /**
     * 标记死信记录已回投。
     *
     * @param id 死信记录主键
     * @return 影响行数
     */
    int markRepublished(@Param("id") Long id);

    /**
     * 增加回投失败次数。
     *
     * @param id 死信记录主键
     * @param failReason 最近失败原因
     * @return 影响行数
     */
    int incrementFailure(@Param("id") Long id,
                         @Param("failReason") String failReason);

    /**
     * 标记死信记录为错误终态。
     *
     * @param id 死信记录主键
     * @param failReason 最终失败原因
     * @param failCount 最终失败次数
     * @return 影响行数
     */
    int markError(@Param("id") Long id,
                  @Param("failReason") String failReason,
                  @Param("failCount") int failCount);

    /**
     * 按状态统计死信记录数量。
     *
     * @param status 死信状态
     * @return 对应状态的记录数
     */
    long countByStatus(@Param("status") int status);
}
