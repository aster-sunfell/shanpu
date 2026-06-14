import { Package } from "lucide-react";

import { GrabButton } from "./grab-button";
import { PointsBadge } from "@/components/points-badge";
import { SeckillCountdown } from "@/components/seckill-countdown";
import { SeckillTierBadge } from "@/components/seckill-tier-badge";
import { Badge } from "@/components/ui/badge";
import { ApiError } from "@/lib/api/client";
import { seckillApi } from "@/lib/api/seckill";
import type { SeckillActivityVO } from "@/types/seckill";

export const dynamic = "force-dynamic";

function stockProgress(activity: SeckillActivityVO) {
  if (activity.totalStock <= 0) return 0;
  const sold = Math.max(0, activity.totalStock - activity.stock);
  return Math.min(100, Math.round((sold / activity.totalStock) * 100));
}

export default async function SeckillDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const activityId = Number(id);
  let activity: SeckillActivityVO | null = null;
  let missing = false;

  try {
    activity = await seckillApi.detail(activityId);
  } catch (e) {
    if (e instanceof ApiError && e.code === 40400) missing = true;
  }

  if (missing || !activity) {
    return (
      <section className="mx-auto max-w-3xl px-4 py-10">
        <div className="rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          {missing ? "秒杀活动不存在。" : "秒杀活动加载失败，请稍后刷新重试。"}
        </div>
      </section>
    );
  }

  const soldOut = activity.stock <= 0;
  const progress = stockProgress(activity);

  return (
    <section className="mx-auto max-w-4xl px-4 py-10">
      <div className="grid gap-6 sm:grid-cols-[minmax(0,1fr)_minmax(280px,360px)]">
        <div className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded-xl bg-muted">
          {activity.coverUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={activity.coverUrl}
              alt={activity.title}
              className="h-full w-full object-cover"
            />
          ) : (
            <Package className="size-16 text-muted-foreground/40" aria-hidden />
          )}
        </div>
        <div className="space-y-5">
          <div className="flex items-center gap-2">
            <SeckillTierBadge tier={activity.tier} />
            <Badge variant={soldOut ? "destructive" : "outline"}>
              {soldOut ? "已售罄" : `库存 ${activity.stock}`}
            </Badge>
          </div>
          <div className="space-y-2">
            <h1 className="text-2xl font-bold tracking-tight">
              {activity.title}
            </h1>
            <p className="text-sm text-muted-foreground">
              {activity.productTitle}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <PointsBadge points={activity.seckillPrice} className="h-7 text-base" />
            <span className="text-sm text-muted-foreground line-through">
              {activity.originalPrice} 积分
            </span>
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>已抢 {progress}%</span>
              <SeckillCountdown startAt={activity.startAt} endAt={activity.endAt} />
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary"
                style={{ width: `${progress}%` }}
                aria-hidden
              />
            </div>
          </div>
          <GrabButton activity={activity} />
        </div>
      </div>
    </section>
  );
}
