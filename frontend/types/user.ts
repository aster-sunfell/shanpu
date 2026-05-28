export interface RegisterReq {
  username: string;
  password: string;
  nickname?: string;
}

export interface LoginReq {
  username: string;
  password: string;
}

export interface UpdateNicknameReq {
  nickname: string;
}

export interface RegisterVO {
  userId: number;
  username: string;
  points: number;
}

export interface LoginVO {
  token: string;
  userId: number;
  username: string;
  points: number;
}

export interface UserVO {
  userId: number;
  username: string;
  nickname: string;
  points: number;
}

export interface PointsVO {
  points: number;
}
