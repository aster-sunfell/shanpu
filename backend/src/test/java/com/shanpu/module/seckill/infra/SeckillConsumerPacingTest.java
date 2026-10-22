package com.shanpu.module.seckill.infra;

import com.shanpu.config.KafkaConfig;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.service.OrderDlqLogService;
import com.shanpu.module.seckill.service.impl.SeckillOrderWriter;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.Test;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.ListenerExecutionFailedException;
import org.springframework.kafka.listener.MessageListenerContainer;
import org.springframework.kafka.support.Acknowledgment;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class SeckillConsumerPacingTest {
    @Test void interruptedWaitDoesNotAcknowledgeOrRecoverToDlq() {
        var writer = mock(SeckillOrderWriter.class);
        var message = SeckillOrderMsg.builder().orderId("1").build();
        var record = new ConsumerRecord<String, SeckillOrderMsg>("orders",0,0,"1",message);
        var interrupted = new OrderConsumptionPacer.ConsumptionInterruptedException(new InterruptedException());
        when(writer.createAsyncPaidOrder(message)).thenThrow(interrupted);
        var ack = mock(Acknowledgment.class);
        assertThrows(OrderConsumptionPacer.ConsumptionInterruptedException.class,
                () -> new SeckillConsumer(writer).consume(record,ack));
        verifyNoInteractions(ack);
        var kafka = mock(KafkaTemplate.class);
        var dlq = mock(OrderDlqLogService.class);
        var container = mock(MessageListenerContainer.class);
        var handler = new KafkaConfig().seckillKafkaErrorHandler(kafka,dlq);
        clearInvocations(kafka,dlq); // Recoverer construction reads producer capabilities.
        assertThrows(org.springframework.kafka.KafkaException.class, () -> handler.handleRemaining(
                new ListenerExecutionFailedException("listener failed",interrupted), List.of(record), null, container));
        verify(container, timeout(3000)).stopAbnormally(any(Runnable.class));
        verifyNoInteractions(kafka,dlq);
    }
}
