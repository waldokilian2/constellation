package com.example.shipping;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST entry points for shipment management.
 */
@RestController
@RequestMapping("/api/shipments")
public class ShipmentController {

    private final ShipmentService shipmentService;

    public ShipmentController(ShipmentService shipmentService) {
        this.shipmentService = shipmentService;
    }

    @PostMapping("/assign")
    public Shipment assign(@RequestBody AssignRequest request) {
        return shipmentService.assign(request.getOrderId(), request.getCarrier());
    }

    @GetMapping("/{orderId}")
    public Shipment getShipment(@PathVariable("orderId") String orderId) {
        return shipmentService.findByOrder(orderId);
    }
}
