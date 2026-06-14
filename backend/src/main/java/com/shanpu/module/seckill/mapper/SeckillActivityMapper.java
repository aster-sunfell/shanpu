package com.shanpu.module.seckill.mapper;

import com.shanpu.module.seckill.domain.entity.SeckillActivity;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;
import java.util.List;

public interface SeckillActivityMapper {

    List<SeckillActivityVO> selectList(@Param("tier") Integer tier,
                                       @Param("status") Integer status);

    SeckillActivityVO selectDetailById(@Param("id") Long id);

    SeckillActivity selectById(@Param("id") Long id);

    int deductStock(@Param("id") Long id);

    List<SeckillActivityVO> selectWarmupList(@Param("now") LocalDateTime now,
                                             @Param("before") LocalDateTime before);

    List<SeckillActivityVO> selectReconcileList(@Param("now") LocalDateTime now,
                                                @Param("after") LocalDateTime after);
}
