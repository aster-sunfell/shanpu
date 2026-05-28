import { request } from "./client";
import type {
  LoginReq,
  LoginVO,
  PointsVO,
  RegisterReq,
  RegisterVO,
  UpdateNicknameReq,
  UserVO,
} from "@/types/user";

export const userApi = {
  register: (body: RegisterReq) =>
    request<RegisterVO>("POST", "/users/register", { body }),
  login: (body: LoginReq) =>
    request<LoginVO>("POST", "/users/login", { body }),
  logout: () => request<void>("POST", "/users/logout"),
  me: () => request<UserVO>("GET", "/users/me"),
  myPoints: () => request<PointsVO>("GET", "/users/me/points"),
  updateNickname: (body: UpdateNicknameReq) =>
    request<UserVO>("POST", "/users/me/nickname", { body }),
};
