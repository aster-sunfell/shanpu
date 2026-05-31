package com.shanpu.module.seckill.service;

public interface OrderDlqLogService {

    void record(String orderId, String failReason);
}
