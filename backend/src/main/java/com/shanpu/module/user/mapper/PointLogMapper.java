package com.shanpu.module.user.mapper;

import com.shanpu.module.user.domain.entity.PointLog;
import org.apache.ibatis.annotations.Param;

import java.util.List;

public interface PointLogMapper {

    int insert(PointLog pointLog);

    int countByBizTypeAndRef(@Param("bizType") int bizType,
                             @Param("bizRef") String bizRef);

    List<PointLog> selectByUserId(@Param("userId") Long userId,
                                  @Param("offset") int offset,
                                  @Param("limit") int limit);
}
