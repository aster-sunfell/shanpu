"use client";

import { Suspense, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Check, Pencil, X } from "lucide-react";
import { toast } from "sonner";

import { AuthGuard } from "@/components/auth-guard";
import { PointsBadge } from "@/components/points-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/hooks/use-auth";
import { userApi } from "@/lib/api/user";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/lib/auth/token-store";
import { ApiError } from "@/types/api";
import type { UserVO } from "@/types/user";

function InfoRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-[96px_minmax(0,1fr)] sm:items-center">
      <span className="text-sm text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function InfoBox({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-10 w-full items-center justify-between gap-2 rounded-lg border bg-muted/20 px-3 py-2",
        className,
      )}
    >
      {children}
    </div>
  );
}

function MePageInner() {
  const storeUser = useAuthStore((s) => s.user);
  const { logout } = useAuth();
  const [me, setMe] = useState<UserVO | null>(
    storeUser
      ? {
          userId: storeUser.userId,
          username: storeUser.username,
          nickname: storeUser.nickname,
          points: storeUser.points,
        }
      : null,
  );
  const [loading, setLoading] = useState(true);
  const [editingNickname, setEditingNickname] = useState(false);
  const [nicknameDraft, setNicknameDraft] = useState("");
  const [savingNickname, setSavingNickname] = useState(false);

  useEffect(() => {
    let active = true;

    async function loadMe() {
      const [profileResult, pointsResult] = await Promise.allSettled([
        userApi.me(),
        userApi.myPoints(),
      ]);
      if (!active) return;

      if (profileResult.status === "fulfilled") {
        const profile = profileResult.value;
        const nextPoints =
          pointsResult.status === "fulfilled"
            ? pointsResult.value.points
            : profile.points;
        const nextMe = { ...profile, points: nextPoints };
        setMe(nextMe);
        useAuthStore.getState().patchUser({
          nickname: nextMe.nickname,
          points: nextMe.points,
        });
      }

      if (pointsResult.status === "fulfilled") {
        setMe((current) =>
          current ? { ...current, points: pointsResult.value.points } : current,
        );
        useAuthStore.getState().updatePoints(pointsResult.value.points);
      }

      const errors = [profileResult, pointsResult]
        .filter((result) => result.status === "rejected")
        .map((result) => result.reason)
        .filter((e) => e instanceof ApiError && e.httpStatus !== 401);
      if (errors[0] instanceof ApiError) toast.error(errors[0].message);

      setLoading(false);
    }

    void loadMe();
    return () => {
      active = false;
    };
  }, []);

  function startNicknameEdit() {
    setNicknameDraft(me?.nickname ?? "");
    setEditingNickname(true);
  }

  function cancelNicknameEdit() {
    setNicknameDraft(me?.nickname ?? "");
    setEditingNickname(false);
  }

  async function saveNickname() {
    if (savingNickname) return;
    setSavingNickname(true);
    try {
      const updated = await userApi.updateNickname({ nickname: nicknameDraft });
      setMe(updated);
      useAuthStore
        .getState()
        .patchUser({ nickname: updated.nickname, points: updated.points });
      setNicknameDraft(updated.nickname);
      setEditingNickname(false);
      toast.success("昵称已保存");
    } catch (e) {
      if (e instanceof ApiError && e.httpStatus !== 401) toast.error(e.message);
    } finally {
      setSavingNickname(false);
    }
  }

  const nicknameText = me?.nickname?.trim() ? me.nickname : "（未设置）";

  return (
    <section className="mx-auto max-w-2xl space-y-4 px-4 py-10">
      <h1 className="text-2xl font-bold tracking-tight">个人中心</h1>
      <Card>
        <CardHeader>
          <CardTitle>账户信息</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <InfoRow label="用户名">
            <InfoBox>
              <span className="font-medium">{me?.username ?? "-"}</span>
            </InfoBox>
          </InfoRow>
          <InfoRow label="昵称">
            <InfoBox className="border-primary/20 bg-primary/5">
              {editingNickname ? (
                <>
                  <Input
                    value={nicknameDraft}
                    onChange={(e) => setNicknameDraft(e.target.value)}
                    maxLength={32}
                    disabled={savingNickname}
                    className="h-7 flex-1 border-0 bg-background px-2 shadow-none focus-visible:ring-2"
                    aria-label="昵称"
                  />
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      type="button"
                      size="icon-sm"
                      onClick={() => void saveNickname()}
                      disabled={savingNickname}
                      aria-label="保存昵称"
                    >
                      <Check className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      size="icon-sm"
                      variant="outline"
                      onClick={cancelNicknameEdit}
                      disabled={savingNickname}
                      aria-label="取消修改昵称"
                    >
                      <X className="size-4" />
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <span className="min-w-0 truncate font-medium">
                    {nicknameText}
                  </span>
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    onClick={startNicknameEdit}
                    disabled={loading || !me}
                    aria-label="修改昵称"
                  >
                    <Pencil className="size-4" />
                  </Button>
                </>
              )}
            </InfoBox>
          </InfoRow>
          <InfoRow label="当前积分">
            <InfoBox>
              <PointsBadge
                points={me?.points ?? "-"}
                className="h-6 border-amber-200 bg-amber-50 text-base text-amber-700"
              />
            </InfoBox>
          </InfoRow>
          <div className="flex gap-2 pt-2">
            <Button
              size="sm"
              variant="destructive"
              onClick={() => void logout()}
            >
              登出
            </Button>
          </div>
        </CardContent>
      </Card>
    </section>
  );
}

export default function MePage() {
  return (
    <Suspense fallback={null}>
      <AuthGuard>
        <MePageInner />
      </AuthGuard>
    </Suspense>
  );
}
