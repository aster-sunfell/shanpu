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
