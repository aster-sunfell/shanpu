"use client";

import Link from "next/link";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuth } from "@/hooks/use-auth";
import { ApiError } from "@/types/api";

const schema = z.object({
  username: z
    .string()
    .regex(/^[a-zA-Z0-9_]{4,32}$/, "用户名为 4-32 位字母数字下划线"),
  password: z.string().min(6, "密码至少 6 位").max(64, "密码最多 64 位"),
});

type FormValues = z.infer<typeof schema>;

function LoginForm() {
  const search = useSearchParams();
  const next = search.get("next") || "/";
  const { login } = useAuth();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    try {
      await login(values.username, values.password, next);
      toast.success("登录成功");
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
      else toast.error("登录失败");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>登录</CardTitle>
        <CardDescription>使用用户名与密码登录闪铺</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={handleSubmit(onSubmit)}
          className="space-y-4"
          noValidate
        >
          <div className="space-y-1.5">
            <Label htmlFor="username">用户名</Label>
            <Input
              id="username"
              autoComplete="username"
              {...register("username")}
            />
            {errors.username && (
              <p className="text-xs text-destructive">
                {errors.username.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">密码</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              {...register("password")}
            />
            {errors.password && (
              <p className="text-xs text-destructive">
                {errors.password.message}
              </p>
            )}
          </div>
          <Button type="submit" disabled={isSubmitting} className="w-full">
            {isSubmitting ? "登录中..." : "登录"}
          </Button>
        </form>
        <p className="mt-4 text-sm text-muted-foreground">
          还没有账号？
          <Link href="/register" className="ml-1 underline underline-offset-2">
            立即注册
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

export default function LoginPage() {
  return (
    <div className="mx-auto max-w-md px-4 py-12">
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
