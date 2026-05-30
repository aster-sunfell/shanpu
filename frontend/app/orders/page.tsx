"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";

import { AuthGuard } from "@/components/auth-guard";
import { OrderStatusBadge } from "@/components/order-status-badge";
import { PointsBadge } from "@/components/points-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { orderApi } from "@/lib/api/order";
import { ApiError } from "@/types/api";
import type { OrderItemVO, OrderStatus } from "@/types/order";

const TABS: { label: string; value: OrderStatus | undefined }[] = [
  { label: "全部", value: undefined },
  { label: "待支付", value: "PENDING" },
  { label: "已支付", value: "PAID" },
  { label: "已取消", value: "CANCELLED" },
];

function OrdersInner() {
  const [status, setStatus] = useState<OrderStatus | undefined>(undefined);
  const [orders, setOrders] = useState<OrderItemVO[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    orderApi
      .list({ status, page: 1, size: 20 })
      .then((page) => {
        if (active) setOrders(page.list);
      })
      .catch((e) => {
        if (active) setOrders([]);
        if (e instanceof ApiError && e.httpStatus !== 401) toast.error(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [status]);

  return (
    <section className="mx-auto max-w-3xl space-y-4 px-4 py-10">
      <h1 className="text-2xl font-bold tracking-tight">我的订单</h1>
      <div className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <Button
            key={t.label}
            size="sm"
            variant={status === t.value ? "default" : "outline"}
            onClick={() => setStatus(t.value)}
          >
            {t.label}
          </Button>
        ))}
      </div>

      {loading ? (
        <p className="py-16 text-center text-sm text-muted-foreground">加载中...</p>
      ) : orders.length === 0 ? (
        <div className="rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          暂无订单。
        </div>
      ) : (
        <div className="space-y-3">
          {orders.map((o) => (
            <Link key={o.orderId} href={`/orders/${o.orderId}`} className="block">
              <Card className="transition-shadow hover:shadow-md">
                <CardContent className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{o.productTitle}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{o.createdAt}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <PointsBadge points={o.price} />
                    <OrderStatusBadge status={o.status} />
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

export default function OrdersPage() {
  return (
    <Suspense fallback={null}>
      <AuthGuard>
        <OrdersInner />
      </AuthGuard>
    </Suspense>
  );
}
