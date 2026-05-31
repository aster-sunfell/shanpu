"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
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
import { userApi } from "@/lib/api/user";
import { ApiError } from "@/types/api";

const schema = z.object({
  username: z
    .string()
    .regex(/^[a-zA-Z0-9_]{4,32}$/, "用户名为 4-32 位字母数字下划线"),
  password: z.string().min(6, "密码至少 6 位").max(64, "密码最多 64 位"),
  nickname: z
    .string()
    .max(32, "昵称最长 32 字符")
    .optional()
    .or(z.literal("")),
});

type FormValues = z.infer<typeof schema>;

export default function RegisterPage() {
  const router = useRouter();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    try {
      await userApi.register({
        username: values.username,
        password: values.password,
        nickname: values.nickname || undefined,
      });
      toast.success("注册成功，请登录");
      router.replace("/login");
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
      else toast.error("注册失败");
    }
  }

  return (
    <div className="mx-auto max-w-md px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle>注册</CardTitle>
          <CardDescription>注册即送 10000 积分</CardDescription>
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
                autoComplete="new-password"
                {...register("password")}
              />
              {errors.password && (
                <p className="text-xs text-destructive">
                  {errors.password.message}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="nickname">昵称（可选）</Label>
              <Input id="nickname" {...register("nickname")} />
              {errors.nickname && (
                <p className="text-xs text-destructive">
                  {errors.nickname.message}
                </p>
              )}
            </div>
            <Button type="submit" disabled={isSubmitting} className="w-full">
              {isSubmitting ? "注册中..." : "注册"}
            </Button>
          </form>
          <p className="mt-4 text-sm text-muted-foreground">
            已有账号？
            <Link href="/login" className="ml-1 underline underline-offset-2">
              立即登录
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
