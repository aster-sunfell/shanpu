"use client";

import { useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useAuthStore } from "@/lib/auth/token-store";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const token = useAuthStore((s) => s.token);
  const hydrated = useAuthStore((s) => s.hydrated);

  useEffect(() => {
    if (!hydrated) return;
    if (!token) {
      const query = search.toString();
      const next = encodeURIComponent(
        pathname + (query ? `?${query}` : ""),
      );
      router.replace(`/login?next=${next}`);
    }
  }, [hydrated, token, pathname, search, router]);

  if (!hydrated) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-12 text-sm text-muted-foreground">
        加载中...
      </div>
    );
  }
  if (!token) return null;
  return <>{children}</>;
}
