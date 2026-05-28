package com.shanpu;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
@MapperScan("com.shanpu.module.*.mapper")
public class ShanpuApplication {

    public static void main(String[] args) {
        SpringApplication.run(ShanpuApplication.class, args);
    }
}
