export type OrderStatus = "PENDING" | "PAID" | "CANCELLED" | "FAILED";

export interface CreateOrderVO {
  orderId: string;
  status: "PAID";
}

export interface OrderItemVO {
  orderId: string;
  productId: number;
  productTitle: string;
  coverUrl: string;
  price: number;
  status: OrderStatus;
  source: number;
  createdAt: string;
}

export interface OrderDetailVO extends OrderItemVO {
  paidAt: string | null;
}
