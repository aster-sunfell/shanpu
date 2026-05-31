package com.shanpu;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@MapperScan("com.shanpu.module.*.mapper")
@EnableScheduling
public class ShanpuApplication {

    public static void main(String[] args) {
        SpringApplication.run(ShanpuApplication.class, args);
    }
}
