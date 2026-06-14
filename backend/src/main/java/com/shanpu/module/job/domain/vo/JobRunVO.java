package com.shanpu.module.job.domain.vo;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class JobRunVO {

    private String job;
    private long scanned;
    private long succeeded;
    private long failed;

    public static JobRunVO of(String job, long scanned, long succeeded, long failed) {
        return JobRunVO.builder()
                .job(job)
                .scanned(scanned)
                .succeeded(succeeded)
                .failed(failed)
                .build();
    }
}
