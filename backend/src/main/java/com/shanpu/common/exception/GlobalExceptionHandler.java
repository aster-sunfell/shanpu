package com.shanpu.common.exception;

import com.shanpu.common.result.Result;
import com.shanpu.common.result.ResultCode;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.stream.Collectors;

@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(BusinessException.class)
    public ResponseEntity<Result<Void>> handleBusiness(BusinessException e) {
        log.warn("Business exception: code={}, msg={}", e.getResultCode().getCode(), e.getMessage());
        return ResponseEntity
                .status(e.getResultCode().getHttpStatus())
                .body(Result.fail(e.getResultCode().getCode(), e.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public Result<Void> handleValidation(MethodArgumentNotValidException e) {
        String detail = e.getBindingResult().getFieldErrors().stream()
                .map(fe -> fe.getField() + ": " + fe.getDefaultMessage())
                .collect(Collectors.joining("; "));
        log.warn("Param invalid: {}", detail);
        return Result.fail(ResultCode.PARAM_INVALID.getCode(), detail);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Result<Void>> handleGeneric(Exception e) {
        log.error("Internal error", e);
        return ResponseEntity
                .status(500)
                .body(Result.fail(ResultCode.INTERNAL_ERROR));
    }
}
