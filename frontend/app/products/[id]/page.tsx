import { Package } from "lucide-react";

import { BuyButton } from "./buy-button";
import { PointsBadge } from "@/components/points-badge";
import { Badge } from "@/components/ui/badge";
import { productApi } from "@/lib/api/product";
import { ApiError } from "@/types/api";
import type { ProductDetailVO } from "@/types/product";

export const dynamic = "force-dynamic";

export default async function ProductDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const productId = Number(id);

  let product: ProductDetailVO | null = null;
  let missing = false;
  try {
    product = await productApi.detail(productId);
  } catch (e) {
    if (e instanceof ApiError && e.code === 40400) missing = true;
  }

  if (missing || !product) {
    return (
      <section className="mx-auto max-w-3xl px-4 py-10">
        <div className="rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          {missing ? "商品不存在或已下架。" : "商品加载失败，请稍后刷新重试。"}
        </div>
      </section>
    );
  }

  const soldOut = product.stock <= 0;

  return (
    <section className="mx-auto max-w-3xl px-4 py-10">
      <div className="grid gap-6 sm:grid-cols-2">
        <div className="flex aspect-square items-center justify-center overflow-hidden rounded-xl bg-muted">
          {product.coverUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={product.coverUrl}
              alt={product.title}
              className="h-full w-full object-cover"
            />
          ) : (
            <Package className="size-16 text-muted-foreground/40" aria-hidden />
          )}
        </div>
        <div className="space-y-4">
          <h1 className="text-2xl font-bold tracking-tight">{product.title}</h1>
          <div className="flex items-center gap-3">
            <PointsBadge points={product.price} className="h-7 text-base" />
            <Badge variant={soldOut ? "destructive" : "outline"}>
              {soldOut ? "已售罄" : `库存 ${product.stock}`}
            </Badge>
          </div>
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
            {product.description || "暂无描述"}
          </p>
          <BuyButton productId={product.id} soldOut={soldOut} />
        </div>
      </div>
    </section>
  );
}
