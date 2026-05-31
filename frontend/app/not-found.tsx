import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function NotFoundPage() {
  return (
    <section className="mx-auto flex max-w-xl flex-col items-center gap-4 px-4 py-20 text-center">
      <h1 className="text-2xl font-bold tracking-tight">页面不存在</h1>
      <p className="text-sm text-muted-foreground">资源不存在或已下架。</p>
      <Button asChild>
        <Link href="/">返回首页</Link>
      </Button>
    </section>
  );
}
