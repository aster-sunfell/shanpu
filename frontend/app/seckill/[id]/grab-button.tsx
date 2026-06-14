"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/use-auth";
import { useCountdown } from "@/hooks/use-countdown";
import { apiErrorMessage } from "@/lib/api/errors";
import { seckillApi } from "@/lib/api/seckill";
import { useAuthStore } from "@/lib/auth/token-store";
import type { SeckillActivityVO } from "@/types/seckill";

export function GrabButton({ activity }: { activity: SeckillActivityVO }) {
  const router = useRouter();
  const token = useAuthStore((s) => s.token);
  const hydrated = useAuthStore((s) => s.hydrated);
  const { refreshPoints } = useAuth();
  const countdown = useCountdown(activity.startAt, activity.endAt);
  const lastClickRef = useRef(0);
  const [submitting, setSubmitting] = useState(false);

  const soldOut = activity.stock <= 0;
  const disabled =
    submitting ||
    soldOut ||
    activity.status === "ENDED" ||
    activity.status === "UPCOMING" ||
    countdown.phase !== "ACTIVE";

  const label = soldOut
    ? "已售罄"
    : activity.status === "UPCOMING" || countdown.phase === "UPCOMING"
      ? "未开始"
      : activity.status === "ENDED" || countdown.phase === "ENDED"
        ? "已结束"
        : submitting
          ? "抢购中..."
          : "立即抢";

  async function grab() {
    const now = Date.now();
    if (now - lastClickRef.current < 1000 || submitting) return;
    lastClickRef.current = now;

    if (hydrated && !token) {
      router.push(`/login?next=${encodeURIComponent(`/seckill/${activity.id}`)}`);
      return;
    }

    setSubmitting(true);
    try {
      const res = await seckillApi.grab(activity.id);
      if (res.status === "PAID") {
        toast.success("抢购成功");
        await refreshPoints();
      } else {
        toast.success("抢购请求已提交");
      }
      router.push(`/orders/${res.orderId}`);
    } catch (e) {
      const message = apiErrorMessage(e, "抢购失败");
      if (message) toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Button
      onClick={() => void grab()}
      disabled={disabled}
      className="w-full sm:w-auto"
    >
      {label}
    </Button>
  );
}
