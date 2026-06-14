"use client";

import Link from "next/link";

import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function SiteHeader() {
  const { isAuthed, user, hydrated, logout } = useAuth();

  return (
    <header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="mx-auto flex min-h-14 max-w-6xl flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:py-0">
        <Link href="/" className="shrink-0 text-lg font-semibold tracking-tight">
          闪铺
        </Link>
        <nav className="flex w-full flex-wrap items-center gap-1 sm:w-auto sm:justify-end sm:gap-3">
          <Button asChild variant="ghost" size="sm">
            <Link href="/products">商品</Link>
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link href="/seckill">秒杀</Link>
          </Button>
          {hydrated && isAuthed ? (
            <Button asChild variant="ghost" size="sm">
              <Link href="/orders">订单</Link>
            </Button>
          ) : null}
          {!hydrated ? (
            <div className="h-8 w-20" aria-hidden />
          ) : isAuthed && user ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="max-w-32 bg-background px-3 sm:max-w-36"
                >
                  <span className="truncate">{user.nickname || user.username}</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem asChild>
                  <Link href="/me">个人中心</Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => void logout()}>
                  登出
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <Button asChild size="sm">
              <Link href="/login">登录</Link>
            </Button>
          )}
        </nav>
      </div>
    </header>
  );
}
