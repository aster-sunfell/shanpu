package com.shanpu.module.user.mapper;

import com.shanpu.module.user.domain.entity.User;
import org.apache.ibatis.annotations.Param;

public interface UserMapper {

    int insert(User user);

    User selectByUsername(@Param("username") String username);

    User selectById(@Param("id") Long id);

    Integer selectPointsById(@Param("id") Long id);

    int updateNickname(@Param("id") Long id, @Param("nickname") String nickname);

    int deductPoints(@Param("id") Long id, @Param("cost") int cost);
}
