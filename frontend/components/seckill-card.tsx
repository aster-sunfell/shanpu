import Link from "next/link";
import { Package } from "lucide-react";

import { PointsBadge } from "@/components/points-badge";
import { SeckillCountdown } from "@/components/seckill-countdown";
import { SeckillTierBadge } from "@/components/seckill-tier-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { SeckillActivityVO } from "@/types/seckill";

function stockProgress(activity: SeckillActivityVO) {
  if (activity.totalStock <= 0) return 0;
  const sold = Math.max(0, activity.totalStock - activity.stock);
  return Math.min(100, Math.round((sold / activity.totalStock) * 100));
}

export function SeckillCard({ activity }: { activity: SeckillActivityVO }) {
  const soldOut = activity.stock <= 0;
  const progress = stockProgress(activity);

  return (
    <Link href={`/seckill/${activity.id}`} className="block h-full">
      <Card className="h-full gap-3 transition-shadow hover:shadow-md">
        <CardContent className="space-y-3">
          <div className="relative flex aspect-[4/3] items-center justify-center overflow-hidden rounded-lg bg-muted">
            {activity.coverUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={activity.coverUrl}
                alt={activity.title}
                className="h-full w-full object-cover"
              />
            ) : (
              <Package className="size-10 text-muted-foreground/40" aria-hidden />
            )}
            <div className="absolute left-2 top-2">
              <SeckillTierBadge tier={activity.tier} />
            </div>
          </div>
          <div className="space-y-1">
            <h3 className="line-clamp-2 min-h-10 text-sm font-medium">
              {activity.title}
            </h3>
            <p className="truncate text-xs text-muted-foreground">
              {activity.productTitle}
            </p>
          </div>
          <div className="flex items-center justify-between gap-2">
            <PointsBadge points={activity.seckillPrice} />
            <Badge variant={soldOut ? "destructive" : "outline"}>
              {soldOut ? "已售罄" : `库存 ${activity.stock}`}
            </Badge>
          </div>
          <div className="space-y-2">
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary"
                style={{ width: `${progress}%` }}
                aria-hidden
              />
            </div>
            <SeckillCountdown startAt={activity.startAt} endAt={activity.endAt} />
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
