import Link from "next/link";

import { ProductCard } from "@/components/product-card";
import { Button } from "@/components/ui/button";
import { productApi } from "@/lib/api/product";
import type { PageResult } from "@/types/api";
import type { ProductVO } from "@/types/product";

export const dynamic = "force-dynamic";

const SIZE = 12;

export default async function ProductsPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);

  let data: PageResult<ProductVO> | null = null;
  try {
    data = await productApi.list({ page, size: SIZE });
  } catch {
    data = null;
  }

  if (!data) {
    return (
      <section className="mx-auto max-w-6xl px-4 py-10">
        <h1 className="text-2xl font-bold tracking-tight">全部商品</h1>
        <div className="mt-6 rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          商品加载失败，请稍后刷新重试。
        </div>
      </section>
    );
  }

  const totalPages = Math.max(1, Math.ceil(data.total / SIZE));

  return (
    <section className="mx-auto max-w-6xl px-4 py-10">
      <h1 className="text-2xl font-bold tracking-tight">全部商品</h1>
      {data.list.length === 0 ? (
        <div className="mt-6 rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          暂无商品。
        </div>
      ) : (
        <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {data.list.map((p) => (
            <ProductCard key={p.id} product={p} />
          ))}
        </div>
      )}

      {totalPages > 1 ? (
        <div className="mt-8 flex items-center justify-center gap-4">
          {page > 1 ? (
            <Button asChild variant="outline" size="sm">
              <Link href={`/products?page=${page - 1}`}>上一页</Link>
            </Button>
          ) : (
            <Button variant="outline" size="sm" disabled>
              上一页
            </Button>
          )}
          <span className="text-sm text-muted-foreground">
            第 {page} / {totalPages} 页
          </span>
          {page < totalPages ? (
            <Button asChild variant="outline" size="sm">
              <Link href={`/products?page=${page + 1}`}>下一页</Link>
            </Button>
          ) : (
            <Button variant="outline" size="sm" disabled>
              下一页
            </Button>
          )}
        </div>
      ) : null}
    </section>
  );
}
