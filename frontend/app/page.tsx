import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <section className="mx-auto max-w-2xl px-4 py-16 text-center">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">闪铺</h1>
      <p className="mt-4 text-base text-muted-foreground sm:text-lg">
        注册即送 10000 积分，参与秒杀活动赢取超值商品。
      </p>
      <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
        <Button asChild>
          <Link href="/register">立即注册</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/login">已有账号，登录</Link>
        </Button>
      </div>
    </section>
  );
}
