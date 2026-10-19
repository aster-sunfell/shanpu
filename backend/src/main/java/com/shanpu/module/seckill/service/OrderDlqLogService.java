package com.shanpu.module.seckill.service;

import com.shanpu.module.seckill.domain.entity.OrderDlqLog;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.job.domain.vo.DlqStatsVO;

import java.util.List;

/**
 * 超级秒杀订单死信日志服务。
 *
 * <p>消费失败进入 DLQ 时记录 payload 和失败原因，回投任务根据这些记录恢复消息。</p>
 */
public interface OrderDlqLogService {

    /**
     * 记录或更新一条死信日志。
     *
     * @param orderId Kafka 记录 key 或订单号
     * @param payload 秒杀订单消息
     * @param failReason 失败原因
     */
    void record(String orderId, SeckillOrderMsg payload, String failReason);

    /**
     * 查询待回投的死信记录。
     *
     * @param limit 本轮最大扫描条数
     * @return 待处理死信记录
     */
    List<OrderDlqLog> listPending(int limit);

    /**
     * 标记死信记录已成功回投。
     *
     * @param id 死信记录主键
     */
    void markRepublished(Long id);

    /**
     * 累加死信记录回投失败次数。
     *
     * @param id 死信记录主键
     * @param failReason 最近失败原因
     */
    void incrementFailure(Long id, String failReason);

    /**
     * 标记死信记录进入错误终态。
     *
     * @param id 死信记录主键
     * @param failReason 最终失败原因
     * @param failCount 最终失败次数
     */
    void markError(Long id, String failReason, int failCount);

    /**
     * 统计死信记录各状态数量。
     *
     * @return 死信统计
     */
    DlqStatsVO stats();
}
