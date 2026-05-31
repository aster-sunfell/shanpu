"use client";

import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { userApi } from "@/lib/api/user";
import { useAuthStore } from "@/lib/auth/token-store";
import { ApiError } from "@/types/api";

export function useAuth() {
  const router = useRouter();
  const token = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);
  const setSession = useAuthStore((s) => s.setSession);
  const clearSession = useAuthStore((s) => s.clearSession);
  const updatePoints = useAuthStore((s) => s.updatePoints);
  const patchUser = useAuthStore((s) => s.patchUser);

  async function login(username: string, password: string, next?: string) {
    const v = await userApi.login({ username, password });
    setSession(v.token, {
      userId: v.userId,
      username: v.username,
      nickname: "",
      points: v.points,
    });
    try {
      const me = await userApi.me();
      patchUser({ nickname: me.nickname, points: me.points });
    } catch {
      // 静默：拿 nickname 失败不阻塞登录跳转
    }
    router.replace(next || "/");
  }

  async function logout() {
    try {
      await userApi.logout();
    } catch {
      // 网络异常也清本地，保护用户
    }
    clearSession();
    toast.success("已登出");
    router.replace("/login");
  }

  async function refreshPoints() {
    try {
      const r = await userApi.myPoints();
      updatePoints(r.points);
    } catch (e) {
      if (e instanceof ApiError && e.httpStatus !== 401) toast.error(e.message);
    }
  }

  return {
    token,
    user,
    hydrated,
    isAuthed: !!token,
    login,
    logout,
    refreshPoints,
  };
}
