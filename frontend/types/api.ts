export interface Result<T> {
  code: number;
  msg: string;
  data: T;
}

export interface PageResult<T> {
  list: T[];
  total: number;
}

export class ApiError extends Error {
  readonly code: number;
  readonly msg: string;
  readonly httpStatus?: number;

  constructor(code: number, msg: string, httpStatus?: number) {
    super(msg);
    this.name = "ApiError";
    this.code = code;
    this.msg = msg;
    this.httpStatus = httpStatus;
  }
}
