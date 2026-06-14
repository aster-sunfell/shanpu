"use client";

import { Clock } from "lucide-react";

import { useCountdown } from "@/hooks/use-countdown";
import { cn } from "@/lib/utils";

export function SeckillCountdown({
  startAt,
  endAt,
  className,
}: {
  startAt: string;
  endAt: string;
  className?: string;
}) {
  const countdown = useCountdown(startAt, endAt);
  const label =
    countdown.phase === "UPCOMING"
      ? "距开始"
      : countdown.phase === "ACTIVE"
        ? "距结束"
        : "已结束";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-xs text-muted-foreground",
        className,
      )}
    >
      <Clock className="size-3.5" aria-hidden />
      <span>{label}</span>
      <span className="font-mono text-foreground">{countdown.text}</span>
    </span>
  );
}
