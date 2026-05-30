"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/use-auth";
import { productApi } from "@/lib/api/product";
import { useAuthStore } from "@/lib/auth/token-store";
import { ApiError } from "@/types/api";

export function BuyButton({
  productId,
  soldOut,
}: {
  productId: number;
  soldOut: boolean;
}) {
  const router = useRouter();
  const { refreshPoints } = useAuth();
  const token = useAuthStore((s) => s.token);
  const hydrated = useAuthStore((s) => s.hydrated);
  const [submitting, setSubmitting] = useState(false);

  async function buy() {
    if (submitting) return;
    if (hydrated && !token) {
      router.push(`/login?next=${encodeURIComponent(`/products/${productId}`)}`);
      return;
    }
    setSubmitting(true);
    try {
      const res = await productApi.order(productId);
      toast.success("兑换成功");
      await refreshPoints();
      router.push(`/orders/${res.orderId}`);
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.httpStatus !== 401) toast.error(e.message);
      } else {
        toast.error("网络异常，请稍后重试");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Button
      onClick={() => void buy()}
      disabled={soldOut || submitting}
      className="w-full sm:w-auto"
    >
      {soldOut ? "已售罄" : submitting ? "兑换中..." : "用积分兑换"}
    </Button>
  );
}
