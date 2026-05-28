package com.shanpu.common.result;

import lombok.Getter;

@Getter
public enum ResultCode {

    SUCCESS(0, "成功", 200),
    PARAM_INVALID(40001, "参数非法", 200),
    UNAUTHORIZED(40100, "未登录或登录已失效", 401),
    FORBIDDEN(40300, "越权访问", 200),
    NOT_FOUND(40400, "资源不存在", 200),
    DUPLICATE_ORDER(40900, "重复下单", 200),
    STOCK_SHORTAGE(41000, "库存不足", 200),
    ACTIVITY_NOT_START(41001, "活动未开始", 200),
    ACTIVITY_ENDED(41002, "活动已结束", 200),
    POINTS_NOT_ENOUGH(41100, "积分不足", 200),
    RATE_LIMITED(42900, "请求过于频繁", 429),
    INTERNAL_ERROR(50000, "服务异常", 500);

    private final int code;
    private final String msg;
    private final int httpStatus;

    ResultCode(int code, String msg, int httpStatus) {
        this.code = code;
        this.msg = msg;
        this.httpStatus = httpStatus;
    }
}
