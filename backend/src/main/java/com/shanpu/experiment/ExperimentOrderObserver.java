package com.shanpu.experiment;

import com.shanpu.module.seckill.domain.event.SeckillOrderPaidEvent;
import lombok.RequiredArgsConstructor;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

/** All experiment hooks depend on the business API, never the other way around. */
@Aspect
@Component
@Profile("experiment")
@RequiredArgsConstructor
public class ExperimentOrderObserver {
    private final ExperimentJournal journal;

    @Before("execution(* com.shanpu.module.seckill.service.impl.SeckillOrderWriter.createSyncPaidOrder(..))")
    public void syncAttempt() {
        journal.attempt(false);
    }

    @Before("execution(* com.shanpu.module.seckill.service.impl.SeckillOrderWriter.createAsyncPaidOrder(..))")
    public void asyncAttempt() {
        journal.attempt(true);
    }

    // Default fallbackExecution=false: an event outside a transaction cannot count as a commit.
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onOrderPaid(SeckillOrderPaidEvent event) {
        journal.recordCommittedOrder(event.activityId(), event.userId(), event.orderId());
    }
}
