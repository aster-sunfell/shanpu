package com.shanpu.experiment;

import com.shanpu.module.seckill.domain.event.SeckillOrderPaidEvent;
import lombok.RequiredArgsConstructor;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.ProceedingJoinPoint;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
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

    private final ExperimentTransactionPressure pressure;

    @Around("execution(* com.shanpu.module.seckill.service.impl.SeckillOrderWriter.createSyncPaidOrder(..)) || execution(* com.shanpu.module.seckill.service.impl.SeckillOrderWriter.createAsyncPaidOrder(..))")
    public Object observe(ProceedingJoinPoint call) throws Throwable {
        boolean async = call.getArgs()[0] instanceof SeckillOrderMsg;
        long id = async ? ((SeckillOrderMsg) call.getArgs()[0]).getActivityId() : (Long) call.getArgs()[2];
        journal.attempt(async);
        Long previous = pressure.enter(id);
        try { return call.proceed(); }
        finally { pressure.leave(previous); }
    }

    // Default fallbackExecution=false: an event outside a transaction cannot count as a commit.
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onOrderPaid(SeckillOrderPaidEvent event) {
        journal.recordCommittedOrder(event.activityId(), event.userId(), event.orderId());
    }
}
