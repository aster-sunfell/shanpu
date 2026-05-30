import { request } from "./client";
import { toQuery } from "./query";
import type { PageResult } from "@/types/api";
import type { OrderDetailVO, OrderItemVO, OrderStatus } from "@/types/order";

export const orderApi = {
  list: (params?: { status?: OrderStatus; page?: number; size?: number }) =>
    request<PageResult<OrderItemVO>>("GET", `/orders${toQuery(params)}`),
  detail: (orderId: string) =>
    request<OrderDetailVO>("GET", `/orders/${orderId}`),
};
