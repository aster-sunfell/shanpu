import { request } from "./client";
import { toQuery } from "./query";
import type { PageResult } from "@/types/api";
import type { CreateOrderVO } from "@/types/order";
import type { ProductDetailVO, ProductVO } from "@/types/product";

export const productApi = {
  list: (params?: { page?: number; size?: number }) =>
    request<PageResult<ProductVO>>("GET", `/products${toQuery(params)}`),
  detail: (id: number) => request<ProductDetailVO>("GET", `/products/${id}`),
  order: (id: number) =>
    request<CreateOrderVO>("POST", `/products/${id}/order`, { body: {} }),
};
