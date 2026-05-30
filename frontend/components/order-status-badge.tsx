import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { OrderStatus } from "@/types/order";

const STATUS_MAP: Record<OrderStatus, { label: string; className: string }> = {
  PENDING: { label: "待支付", className: "border-amber-200 bg-amber-50 text-amber-700" },
  PAID: { label: "已支付", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  CANCELLED: { label: "已取消", className: "border-border bg-muted text-muted-foreground" },
  FAILED: { label: "已失败", className: "border-red-200 bg-red-50 text-red-700" },
};

export function OrderStatusBadge({
  status,
  className,
}: {
  status: OrderStatus;
  className?: string;
}) {
  const s = STATUS_MAP[status] ?? STATUS_MAP.PENDING;
  return (
    <Badge variant="secondary" className={cn(s.className, className)}>
      {s.label}
    </Badge>
  );
}
