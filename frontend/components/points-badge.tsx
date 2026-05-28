"use client";

import { Coins } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/lib/auth/token-store";

interface PointsBadgeProps {
  points?: number | string;
  className?: string;
}

export function PointsBadge({ points, className }: PointsBadgeProps) {
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);
  const displayPoints = points ?? user?.points;

  if (!hydrated && points === undefined) return null;
  if (displayPoints === undefined || displayPoints === null) return null;

  return (
    <Badge
      variant="secondary"
      className={cn(
        "gap-1.5 border-amber-200 bg-amber-50 text-amber-700",
        className,
      )}
    >
      <Coins className="size-3.5 text-amber-500" aria-hidden />
      <span>{displayPoints}</span>
    </Badge>
  );
}
