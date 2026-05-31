import { Badge } from "@/components/ui/badge";
import type { SeckillTier } from "@/types/seckill";

const TIER_LABEL: Record<SeckillTier, string> = {
  1: "普惠",
  2: "限时",
  3: "超级",
};

export function SeckillTierBadge({ tier }: { tier: SeckillTier }) {
  if (tier === 1) {
    return (
      <Badge className="border-emerald-200 bg-emerald-50 text-emerald-700">
        {TIER_LABEL[tier]}
      </Badge>
    );
  }
  if (tier === 2) {
    return (
      <Badge className="border-amber-200 bg-amber-50 text-amber-700">
        {TIER_LABEL[tier]}
      </Badge>
    );
  }
  return <Badge variant="destructive">{TIER_LABEL[tier]}</Badge>;
}
