export type SeckillTier = 1 | 2 | 3;
export type SeckillStatus = "UPCOMING" | "ACTIVE" | "ENDED";
export type SeckillGrabStatus = "PAID" | "PENDING";

export interface SeckillActivityVO {
  id: number;
  productId: number;
  productTitle: string;
  coverUrl: string;
  title: string;
  tier: SeckillTier;
  originalPrice: number;
  seckillPrice: number;
  totalStock: number;
  stock: number;
  status: SeckillStatus;
  statusCode: number;
  startAt: string;
  endAt: string;
}

export interface SeckillGrabVO {
  orderId: string;
  status: SeckillGrabStatus;
  tier: SeckillTier;
}
