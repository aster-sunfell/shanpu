package com.shanpu.module.seckill.service;

import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import com.shanpu.module.seckill.domain.vo.SeckillGrabVO;

import java.util.List;

public interface SeckillService {

    List<SeckillActivityVO> list(Integer tier, String status);

    SeckillActivityVO detail(Long activityId);

    SeckillGrabVO grab(Long userId, Long activityId);
}
