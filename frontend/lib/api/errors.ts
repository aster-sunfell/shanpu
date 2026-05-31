import { ApiError } from "@/lib/api/client";

export function apiErrorMessage(error: unknown, fallback = "操作失败") {
  if (error == null) return null;
  if (error instanceof ApiError) {
    if (error.httpStatus === 401) return null;
    if (error.code === 42900) return "操作过于频繁";
    if (error.code === 0) return "网络异常，请稍后重试";
    return error.message || fallback;
  }
  return "网络异常，请稍后重试";
}
