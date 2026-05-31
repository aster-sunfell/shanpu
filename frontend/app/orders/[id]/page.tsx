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
import { ApiError } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import { orderApi } from "@/lib/api/order";
import type { OrderDetailVO } from "@/types/order";

type DetailState =
  | { orderId: string; status: "loading"; order: null; error: null }
  | { orderId: string; status: "ready"; order: OrderDetailVO; error: null }
  | { orderId: string; status: "missing"; order: null; error: null }
  | { orderId: string; status: "error"; order: null; error: string };

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
  const [state, setState] = useState<DetailState>({
    orderId,
    status: "loading",
    order: null,
    error: null,
  });

  useEffect(() => {
    let active = true;
    orderApi
      .detail(orderId)
      .then((o) => {
        if (active) {
          setState({ orderId, status: "ready", order: o, error: null });
        }
      })
      .catch((e) => {
        if (!active) return;
        if (e instanceof ApiError && e.code === 40400) {
          setState({ orderId, status: "missing", order: null, error: null });
        } else {
          const message = apiErrorMessage(e);
          if (message) {
            setState({ orderId, status: "error", order: null, error: message });
            toast.error(message);
          }
        }
      });
    return () => {
      active = false;
    };
  }, [orderId]);

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
