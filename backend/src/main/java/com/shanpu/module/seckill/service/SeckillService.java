package com.shanpu.module.seckill.service;

import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import com.shanpu.module.seckill.domain.vo.SeckillGrabVO;

import java.util.List;

public interface SeckillService {

    /**
     * 查询秒杀活动列表。
     *
     * @param tier 可选档位过滤
     * @param status 可选状态过滤
     * @return 活动列表
     */
    List<SeckillActivityVO> list(Integer tier, String status);

    /**
     * 查询秒杀活动详情。
     *
     * @param activityId 秒杀活动 ID
     * @return 活动详情
     */
    SeckillActivityVO detail(Long activityId);

    /**
     * 执行秒杀抢购。
     *
     * @param userId 当前登录用户 ID
     * @param activityId 秒杀活动 ID
     * @return 抢购结果
     */
    SeckillGrabVO grab(Long userId, Long activityId);
}
