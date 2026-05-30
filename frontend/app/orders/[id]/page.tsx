"use client";

import { Suspense, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";

import { AuthGuard } from "@/components/auth-guard";
import { OrderStatusBadge } from "@/components/order-status-badge";
import { PointsBadge } from "@/components/points-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { orderApi } from "@/lib/api/order";
import { ApiError } from "@/types/api";
import type { OrderDetailVO } from "@/types/order";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[80px_minmax(0,1fr)] items-center gap-2">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function OrderDetailInner() {
  const params = useParams<{ id: string }>();
  const orderId = params.id;
  const [order, setOrder] = useState<OrderDetailVO | null>(null);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let active = true;
    orderApi
      .detail(orderId)
      .then((o) => {
        if (active) setOrder(o);
      })
      .catch((e) => {
        if (!active) return;
        if (e instanceof ApiError && e.code === 40400) setMissing(true);
        else if (e instanceof ApiError && e.httpStatus !== 401) toast.error(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [orderId]);

  if (loading) {
    return <p className="py-16 text-center text-sm text-muted-foreground">加载中...</p>;
  }

  if (missing || !order) {
    return (
      <div className="space-y-4">
        <div className="rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          订单不存在。
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/orders">返回订单列表</Link>
        </Button>
      </div>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>订单详情</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Row label="订单号">
          <span className="truncate font-mono text-xs">{order.orderId}</span>
        </Row>
        <Row label="商品">
          <span className="font-medium">{order.productTitle}</span>
        </Row>
        <Row label="支付积分">
          <PointsBadge points={order.price} />
        </Row>
        <Row label="状态">
          <OrderStatusBadge status={order.status} />
        </Row>
        <Row label="下单时间">
          <span className="text-muted-foreground">{order.createdAt}</span>
        </Row>
        {order.paidAt ? (
          <Row label="支付时间">
            <span className="text-muted-foreground">{order.paidAt}</span>
          </Row>
        ) : null}
        <div className="pt-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/orders">返回订单列表</Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function OrderDetailPage() {
  return (
    <section className="mx-auto max-w-2xl px-4 py-10">
      <Suspense fallback={null}>
        <AuthGuard>
          <OrderDetailInner />
        </AuthGuard>
      </Suspense>
    </section>
  );
}
