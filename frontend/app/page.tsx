import Link from "next/link";

import { ProductCard } from "@/components/product-card";
import { Button } from "@/components/ui/button";
import { productApi } from "@/lib/api/product";
import type { ProductVO } from "@/types/product";

export const dynamic = "force-dynamic";

async function loadHotProducts(): Promise<ProductVO[]> {
  try {
    const page = await productApi.list({ page: 1, size: 8 });
    return page.list;
  } catch {
    return [];
  }
}

export default async function Home() {
  const products = await loadHotProducts();

  return (
    <div className="mx-auto max-w-6xl px-4 py-10">
      <section className="rounded-2xl bg-muted/40 px-6 py-12 text-center">
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">闪铺</h1>
        <p className="mt-4 text-base text-muted-foreground sm:text-lg">
          注册即送 10000 积分，用积分兑换心仪好物。
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Button asChild>
            <Link href="/products">去逛逛</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/register">立即注册</Link>
          </Button>
        </div>
      </section>

      <section className="mt-10">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold tracking-tight">热门商品</h2>
          <Link
            href="/products"
            className="text-sm text-muted-foreground underline-offset-2 hover:underline"
          >
            查看全部
          </Link>
        </div>
        {products.length === 0 ? (
          <div className="rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
            暂无商品，请稍后再试。
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {products.map((p) => (
              <ProductCard key={p.id} product={p} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
