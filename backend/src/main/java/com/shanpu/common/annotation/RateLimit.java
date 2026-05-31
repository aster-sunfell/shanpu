package com.shanpu.common.annotation;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface RateLimit {

    int window() default 1;

    int max() default 10;

    Dimension dim() default Dimension.USER;

    String key() default "";

    enum Dimension {
        USER,
        IP,
        API
    }
}
