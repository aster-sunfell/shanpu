import Link from "next/link";
import { Package } from "lucide-react";

import { PointsBadge } from "@/components/points-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { ProductVO } from "@/types/product";

export function ProductCard({ product }: { product: ProductVO }) {
  const soldOut = product.stock <= 0;
  return (
    <Link href={`/products/${product.id}`} className="block h-full">
      <Card className="h-full gap-3 transition-shadow hover:shadow-md">
        <CardContent className="space-y-3">
          <div className="flex aspect-square items-center justify-center overflow-hidden rounded-lg bg-muted">
            {product.coverUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={product.coverUrl}
                alt={product.title}
                className="h-full w-full object-cover"
              />
            ) : (
              <Package className="size-10 text-muted-foreground/40" aria-hidden />
            )}
          </div>
          <h3 className="line-clamp-2 min-h-10 text-sm font-medium">
            {product.title}
          </h3>
          <div className="flex items-center justify-between">
            <PointsBadge points={product.price} />
            <Badge variant={soldOut ? "destructive" : "outline"}>
              {soldOut ? "已售罄" : `库存 ${product.stock}`}
            </Badge>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
