import { request } from "./client";
import { toQuery } from "./query";
import type {
  SeckillActivityVO,
  SeckillGrabVO,
  SeckillStatus,
  SeckillTier,
} from "@/types/seckill";

export const seckillApi = {
  list: (params?: { tier?: SeckillTier; status?: SeckillStatus }) =>
    request<SeckillActivityVO[]>("GET", `/seckills${toQuery(params)}`),
  detail: (id: number) => request<SeckillActivityVO>("GET", `/seckills/${id}`),
  grab: (id: number) =>
    request<SeckillGrabVO>("POST", `/seckills/${id}/grab`, { body: {} }),
};
