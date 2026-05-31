import { request } from "./client";
import { getToken } from "@/lib/auth/token-store";
import type {
  LoginReq,
  LoginVO,
  PointsVO,
  RegisterReq,
  RegisterVO,
  UpdateNicknameReq,
  UserVO,
} from "@/types/user";

const POINTS_CACHE_TTL_MS = 5000;

let pointsCache:
  | {
      token: string | null;
      value: PointsVO;
      expiresAt: number;
    }
  | null = null;

async function myPoints(options?: { force?: boolean }) {
  const token = typeof window === "undefined" ? null : getToken();
  const now = Date.now();
  if (
    !options?.force &&
    pointsCache &&
    pointsCache.token === token &&
    pointsCache.expiresAt > now
  ) {
    return pointsCache.value;
  }

  const value = await request<PointsVO>("GET", "/users/me/points");
  pointsCache = {
    token,
    value,
    expiresAt: now + POINTS_CACHE_TTL_MS,
  };
  return value;
}

export const userApi = {
  register: (body: RegisterReq) =>
    request<RegisterVO>("POST", "/users/register", { body }),
  login: (body: LoginReq) =>
    request<LoginVO>("POST", "/users/login", { body }),
  logout: () => request<void>("POST", "/users/logout"),
  me: () => request<UserVO>("GET", "/users/me"),
  myPoints,
  updateNickname: (body: UpdateNicknameReq) =>
    request<UserVO>("POST", "/users/me/nickname", { body }),
};
