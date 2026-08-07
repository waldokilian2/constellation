package com.example.fulfillment;

import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

@Service
public class FulfillmentService {

    private final KafkaTemplate<String, Object> kafkaTemplate;
    private final ShipmentRepository shipmentRepository;

    public FulfillmentService(KafkaTemplate<String, Object> kafkaTemplate,
                              ShipmentRepository shipmentRepository) {
        this.kafkaTemplate = kafkaTemplate;
        this.shipmentRepository = shipmentRepository;
    }

    @RabbitListener(queues = "order-events")
    public void handleOrderMessage(OrderMessage message) {
        Shipment shipment = new Shipment(message.getOrderId());
        shipment.schedule();
        shipmentRepository.save(shipment);
        kafkaTemplate.send("shipment-events", new ShipmentCreatedEvent(shipment.getId()));
    }

    @RabbitListener(queues = "payment-events")
    public void handlePaymentEvent(PaymentEvent event) {
        if (event.getStatus().equals("CONFIRMED")) {
            Shipment shipment = shipmentRepository.findByOrderId(event.getOrderId());
            if (shipment != null) {
                shipment.release();
                kafkaTemplate.send("shipment-events", new ShipmentReadyEvent(shipment.getId()));
            }
        }
    }
}
