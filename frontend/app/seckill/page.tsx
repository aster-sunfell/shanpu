import Link from "next/link";

import { SeckillCard } from "@/components/seckill-card";
import { Button } from "@/components/ui/button";
import { seckillApi } from "@/lib/api/seckill";
import type { SeckillActivityVO, SeckillTier } from "@/types/seckill";

export const dynamic = "force-dynamic";

const TIER_OPTIONS: Array<{ label: string; value?: SeckillTier }> = [
  { label: "全部" },
  { label: "普惠", value: 1 },
  { label: "限时", value: 2 },
  { label: "超级", value: 3 },
];

function parseTier(value?: string): SeckillTier | undefined {
  const n = Number(value);
  return n === 1 || n === 2 || n === 3 ? n : undefined;
}

export default async function SeckillPage({
  searchParams,
}: {
  searchParams: Promise<{ tier?: string }>;
}) {
  const sp = await searchParams;
  const tier = parseTier(sp.tier);

  let activities: SeckillActivityVO[] | null = null;
  try {
    activities = await seckillApi.list({ tier });
  } catch {
    activities = null;
  }

  const active = activities?.filter((a) => a.status === "ACTIVE") ?? [];
  const upcoming = activities?.filter((a) => a.status === "UPCOMING") ?? [];

  return (
    <section className="mx-auto max-w-6xl px-4 py-10">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">秒杀专区</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            三档活动统一入口，按活动响应的档位渲染。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {TIER_OPTIONS.map((option) => {
            const selected = option.value === tier || (!option.value && !tier);
            const href = option.value ? `/seckill?tier=${option.value}` : "/seckill";
            return (
              <Button
                key={option.label}
                asChild
                size="sm"
                variant={selected ? "default" : "outline"}
              >
                <Link href={href}>{option.label}</Link>
              </Button>
            );
          })}
        </div>
      </div>

      {!activities ? (
        <div className="mt-6 rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          秒杀活动加载失败，请稍后刷新重试。
        </div>
      ) : (
        <div className="mt-8 space-y-10">
          <ActivitySection title="正在秒杀" activities={active} />
          <ActivitySection title="即将开始" activities={upcoming} />
        </div>
      )}
    </section>
  );
}

function ActivitySection({
  title,
  activities,
}: {
  title: string;
  activities: SeckillActivityVO[];
}) {
  return (
    <section>
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      {activities.length === 0 ? (
        <div className="mt-4 rounded-xl border border-dashed py-12 text-center text-sm text-muted-foreground">
          暂无活动。
        </div>
      ) : (
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {activities.map((activity) => (
            <SeckillCard key={activity.id} activity={activity} />
          ))}
        </div>
      )}
    </section>
  );
}
