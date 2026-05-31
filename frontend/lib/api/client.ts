import type { Result } from "@/types/api";
import { ApiError } from "@/lib/api/api-error";
import { readPublicConfig } from "@/lib/config/public-config";
import { getToken, useAuthStore } from "@/lib/auth/token-store";

export { ApiError } from "@/lib/api/api-error";

type Method = "GET" | "POST";

interface RequestOptions {
  body?: unknown;
  signal?: AbortSignal;
}

let onUnauthorized: () => void = () => {
  if (typeof window === "undefined") return;
  const next = encodeURIComponent(
    window.location.pathname + window.location.search,
  );
  window.location.href = `/login?next=${next}`;
};

export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export async function request<T>(
  method: Method,
  path: string,
  options?: RequestOptions,
): Promise<T> {
  const base = readPublicConfig().apiBase;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (typeof window !== "undefined") {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  let resp: Response;
  try {
    resp = await fetch(base + path, {
      method,
      headers,
      body:
        options?.body !== undefined && method !== "GET"
          ? JSON.stringify(options.body)
          : undefined,
      signal: options?.signal,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "网络异常，请稍后重试");
  }

  if (resp.status === 401) {
    useAuthStore.getState().clearSession();
    onUnauthorized();
    throw new ApiError(40100, "请先登录", 401);
  }
  if (resp.status === 429) {
    throw new ApiError(42900, "操作过于频繁", 429);
  }

  let body: Result<T>;
  try {
    body = (await resp.json()) as Result<T>;
  } catch {
    throw new ApiError(0, "网络异常，请稍后重试", resp.status);
  }

  if (body.code !== 0) {
    throw new ApiError(body.code, body.msg ?? "未知错误", resp.status);
  }
  return body.data;
}
