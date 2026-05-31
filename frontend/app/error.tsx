"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <section className="mx-auto flex max-w-xl flex-col items-center gap-4 px-4 py-20 text-center">
      <h1 className="text-2xl font-bold tracking-tight">页面加载失败</h1>
      <p className="text-sm text-muted-foreground">网络异常，请稍后重试</p>
      <div className="flex gap-2">
        <Button type="button" onClick={reset}>
          重新加载
        </Button>
        <Button asChild variant="outline">
          <Link href="/">返回首页</Link>
        </Button>
      </div>
    </section>
  );
}
