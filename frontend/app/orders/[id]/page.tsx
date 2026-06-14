"use client";

import { Suspense, useEffect, type ReactNode } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";

import { AuthGuard } from "@/components/auth-guard";
import { OrderStatusBadge } from "@/components/order-status-badge";
import { PointsBadge } from "@/components/points-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useSeckillResult } from "@/hooks/use-seckill-result";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[80px_minmax(0,1fr)] sm:items-center sm:gap-2">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function OrderDetailInner() {
  const params = useParams<{ id: string }>();
  const orderId = params.id;
  const { state, refresh } = useSeckillResult(orderId);

  useEffect(() => {
    if (state.status === "error") {
      toast.error(state.error);
    }
  }, [state.status, state.error]);

  if (state.orderId !== orderId || state.status === "loading") {
    return <p className="py-16 text-center text-sm text-muted-foreground">加载中...</p>;
  }

  if (state.status === "error") {
    return (
      <div className="space-y-4">
        <div className="rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          {state.error}
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/orders">返回订单列表</Link>
        </Button>
      </div>
    );
  }

  if (state.status === "missing") {
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

  const order = state.order;

  return (
    <Card>
      <CardHeader>
        <CardTitle>订单详情</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Row label="订单号">
          <span className="break-all font-mono text-xs">{order.orderId}</span>
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
        {order.source === 4 && order.status === "PENDING" ? (
          <div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
            {state.exhausted ? "订单仍在确认中。" : "订单确认中..."}
          </div>
        ) : null}
        {order.source === 4 && order.status === "CANCELLED" ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            抢购未完成。
          </div>
        ) : null}
        <div className="flex flex-col gap-2 pt-2 sm:flex-row">
          {state.exhausted ? (
            <Button variant="default" size="sm" onClick={refresh}>
              手动刷新
            </Button>
          ) : null}
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
