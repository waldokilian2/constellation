"""Mock Constellation backend for frontend verification."""
import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

WEB = Path(__file__).parent

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

GRAPH = {
    "repos": ["order-service", "payment-service", "stock-service", "notification-service"],
    "entry_points": [
        {
            "id": "order-service:OrderController.create",
            "repo": "order-service", "type": "rest-endpoint", "channel": "/orders",
            "class_name": "OrderController", "method": "create",
            "file": "/repos/order-service/src/main/java/com/acme/order/OrderController.java", "line": 37,
            "message_type": "Order",
            "call_tree": {
                "method": "OrderController.create", "file": "/repos/order-service/src/main/java/com/acme/order/OrderController.java",
                "line": 37, "class_name": "OrderController", "confidence": "EXTRACTED",
                "children": [
                    {"method": "OrderService.validate", "file": "/repos/order-service/src/main/java/com/acme/order/OrderService.java", "line": 54, "class_name": "OrderService", "confidence": "EXTRACTED", "children": [
                        {"method": "OrderRepository.existsById", "file": "/repos/order-service/src/main/java/com/acme/order/OrderRepository.java", "line": 12, "class_name": "OrderRepository", "confidence": "INFERRED", "children": []},
                    ]},
                    {"method": "OrderService.save", "file": "/repos/order-service/src/main/java/com/acme/order/OrderService.java", "line": 68, "class_name": "OrderService", "confidence": "EXTRACTED", "children": []},
                    {"method": "KafkaTemplate.send", "file": "/repos/order-service/src/main/java/com/acme/order/OrderController.java", "line": 40, "class_name": "", "confidence": "INFERRED", "children": []},
                ],
            },
            "metrics": {"depth": 3, "total_nodes": 7, "unique_files": 3, "branch_count": 1},
        },
        {
            "id": "order-service:OrderController.getById",
            "repo": "order-service", "type": "rest-endpoint", "channel": "/orders/{id}",
            "class_name": "OrderController", "method": "getById",
            "file": "/repos/order-service/src/main/java/com/acme/order/OrderController.java", "line": 55,
            "message_type": "",
            "call_tree": {
                "method": "OrderController.getById", "file": "/repos/order-service/src/main/java/com/acme/order/OrderController.java",
                "line": 55, "class_name": "OrderController", "confidence": "EXTRACTED",
                "children": [
                    {"method": "OrderRepository.findById", "file": "/repos/order-service/src/main/java/com/acme/order/OrderRepository.java", "line": 20, "class_name": "OrderRepository", "confidence": "INFERRED", "children": []},
                ],
            },
            "metrics": {"depth": 2, "total_nodes": 3, "unique_files": 2, "branch_count": 0},
        },
        {
            "id": "order-service:OrderEventListener.onOrderShipped",
            "repo": "order-service", "type": "event-listener", "channel": "order-shipped",
            "class_name": "OrderEventListener", "method": "onOrderShipped",
            "file": "/repos/order-service/src/main/java/com/acme/order/OrderEventListener.java", "line": 22,
            "message_type": "OrderShippedEvent",
            "call_tree": {
                "method": "OrderEventListener.onOrderShipped", "file": "/repos/order-service/src/main/java/com/acme/order/OrderEventListener.java",
                "line": 22, "class_name": "OrderEventListener", "confidence": "EXTRACTED",
                "children": [
                    {"method": "NotificationService.sendShipped", "file": "/repos/order-service/src/main/java/com/acme/order/NotificationService.java", "line": 30, "class_name": "NotificationService", "confidence": "INFERRED", "children": []},
                ],
            },
            "metrics": {"depth": 2, "total_nodes": 2, "unique_files": 2, "branch_count": 0},
        },
        {
            "id": "payment-service:PaymentApp.onOrder",
            "repo": "payment-service", "type": "kafka-consumer", "channel": "orders",
            "class_name": "PaymentApp", "method": "onOrder",
            "file": "/repos/payment-service/src/main/java/com/acme/payment/PaymentApp.java", "line": 45,
            "message_type": "Order",
            "call_tree": {
                "method": "PaymentApp.onOrder", "file": "/repos/payment-service/src/main/java/com/acme/payment/PaymentApp.java",
                "line": 45, "class_name": "PaymentApp", "confidence": "EXTRACTED",
                "children": [
                    {"method": "PaymentProcessor.charge", "file": "/repos/payment-service/src/main/java/com/acme/payment/PaymentProcessor.java", "line": 60, "class_name": "PaymentProcessor", "confidence": "EXTRACTED", "children": [
                        {"method": "PaymentGateway.call", "file": "/repos/payment-service/src/main/java/com/acme/payment/PaymentGateway.java", "line": 28, "class_name": "PaymentGateway", "confidence": "INFERRED", "children": []},
                        {"method": "TransactionRepository.save", "file": "/repos/payment-service/src/main/java/com/acme/payment/TransactionRepository.java", "line": 15, "class_name": "TransactionRepository", "confidence": "INFERRED", "children": []},
                    ]},
                    {"method": "RabbitTemplate.convertAndSend", "file": "/repos/payment-service/src/main/java/com/acme/payment/PaymentApp.java", "line": 52, "class_name": "", "confidence": "INFERRED", "children": []},
                ],
            },
            "metrics": {"depth": 3, "total_nodes": 5, "unique_files": 4, "branch_count": 1},
        },
        {
            "id": "stock-service:StockApp.onOrder",
            "repo": "stock-service", "type": "kafka-consumer", "channel": "orders",
            "class_name": "StockApp", "method": "onOrder",
            "file": "/repos/stock-service/src/main/java/com/acme/stock/StockApp.java", "line": 30,
            "message_type": "Order",
            "call_tree": {
                "method": "StockApp.onOrder", "file": "/repos/stock-service/src/main/java/com/acme/stock/StockApp.java",
                "line": 30, "class_name": "StockApp", "confidence": "EXTRACTED",
                "children": [
                    {"method": "InventoryService.reserve", "file": "/repos/stock-service/src/main/java/com/acme/stock/InventoryService.java", "line": 40, "class_name": "InventoryService", "confidence": "EXTRACTED", "children": []},
                ],
            },
            "metrics": {"depth": 2, "total_nodes": 2, "unique_files": 2, "branch_count": 0},
        },
        {
            "id": "notification-service:NotificationConsumer.onMessage",
            "repo": "notification-service", "type": "rabbitmq-consumer", "channel": "payment-events",
            "class_name": "NotificationConsumer", "method": "onMessage",
            "file": "/repos/notification-service/src/main/java/com/acme/notif/NotificationConsumer.java", "line": 28,
            "message_type": "",
            "call_tree": {
                "method": "NotificationConsumer.onMessage", "file": "/repos/notification-service/src/main/java/com/acme/notif/NotificationConsumer.java",
                "line": 28, "class_name": "NotificationConsumer", "confidence": "EXTRACTED",
                "children": [
                    {"method": "EmailSender.send", "file": "/repos/notification-service/src/main/java/com/acme/notif/EmailSender.java", "line": 35, "class_name": "EmailSender", "confidence": "INFERRED", "children": []},
                    {"method": "TemplateEngine.render", "file": "/repos/notification-service/src/main/java/com/acme/notif/TemplateEngine.java", "line": 22, "class_name": "TemplateEngine", "confidence": "INFERRED", "children": []},
                ],
            },
            "metrics": {"depth": 2, "total_nodes": 3, "unique_files": 3, "branch_count": 0},
        },
        {
            "id": "notification-service:NotificationController.sendTest",
            "repo": "notification-service", "type": "rest-endpoint", "channel": "/notify/test",
            "class_name": "NotificationController", "method": "sendTest",
            "file": "/repos/notification-service/src/main/java/com/acme/notif/NotificationController.java", "line": 25,
            "message_type": "",
            "call_tree": {
                "method": "NotificationController.sendTest", "file": "/repos/notification-service/src/main/java/com/acme/notif/NotificationController.java",
                "line": 25, "class_name": "NotificationController", "confidence": "EXTRACTED",
                "children": [
                    {"method": "EmailSender.send", "file": "/repos/notification-service/src/main/java/com/acme/notif/EmailSender.java", "line": 35, "class_name": "EmailSender", "confidence": "INFERRED", "children": []},
                ],
            },
            "metrics": {"depth": 2, "total_nodes": 2, "unique_files": 2, "branch_count": 0},
        },
    ],
    "producers": [
        {"id": "order-service:OrderController.create:send", "repo": "order-service", "type": "kafka-producer", "channel": "orders", "method": "OrderController.create", "file": "/repos/order-service/src/main/java/com/acme/order/OrderController.java", "line": 40, "message_type": "Order"},
        {"id": "payment-service:PaymentApp.onOrder:send", "repo": "payment-service", "type": "rabbitmq-producer", "channel": "payment-events", "method": "PaymentApp.onOrder", "file": "/repos/payment-service/src/main/java/com/acme/payment/PaymentApp.java", "line": 52, "message_type": ""},
    ],
    "cross_repo_links": [
        {"channel": "orders", "producers": ["order-service:OrderController.create:send"], "consumers": ["payment-service:PaymentApp.onOrder", "stock-service:StockApp.onOrder"]},
        {"channel": "payment-events", "producers": ["payment-service:PaymentApp.onOrder:send"], "consumers": ["notification-service:NotificationConsumer.onMessage"]},
    ],
    "generated_at": "2026-08-05T20:14:00Z",
    "engine_version": "0.1.0",
}

SAMPLE_SOURCE = """package com.acme.order;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService orderService;
    private final KafkaTemplate<String, Object> kafka;

    public OrderController(OrderService orderService, KafkaTemplate<String, Object> kafka) {
        this.orderService = orderService;
        this.kafka = kafka;
    }

    @PostMapping
    public ResponseEntity<Order> create(@RequestBody Order order) {
        // Validate and persist the incoming order
        orderService.validate(order);
        Order saved = orderService.save(order);
        kafka.send("orders", saved);
        return ResponseEntity.ok(saved);
    }

    @GetMapping("/{id}")
    public ResponseEntity<Order> getById(@PathVariable Long id) {
        return orderRepository.findById(id)
            .map(ResponseEntity::ok)
            .orElse(ResponseEntity.notFound().build());
    }
}
"""

@app.get("/api/graph")
async def get_graph():
    return JSONResponse(GRAPH)

@app.get("/api/graph/entry-points")
async def get_entry_points():
    return JSONResponse(GRAPH["entry_points"])

@app.get("/api/graph/entry-point/{ep_id}")
async def get_entry_point(ep_id: str):
    for ep in GRAPH["entry_points"]:
        if ep["id"] == ep_id:
            return JSONResponse(ep)
    return JSONResponse({"detail": "not found"}, status_code=404)

@app.get("/api/graph/cross-repo-links")
async def get_links():
    return JSONResponse(GRAPH["cross_repo_links"])

@app.get("/api/graph/repos")
async def get_repos():
    out = []
    for r in GRAPH["repos"]:
        out.append({
            "name": r,
            "entry_points": [e for e in GRAPH["entry_points"] if e["repo"] == r],
            "producers": [p for p in GRAPH["producers"] if p["repo"] == r],
        })
    return JSONResponse(out)

@app.get("/api/source")
async def get_source(file_path: str):
    return JSONResponse({
        "file": file_path,
        "content": SAMPLE_SOURCE,
        "lines": SAMPLE_SOURCE.split("\\n"),
        "line_count": len(SAMPLE_SOURCE.split("\\n")),
    })

@app.post("/api/ai/explain")
async def ai_explain(request: Request):
    body = await request.json()
    return JSONResponse({
        "available": True,
        "response": f"\\nThis is a mock AI explanation for '{body.get('function_name', 'function')}'.\\n\\n"
                    f"In response to: {body.get('question', '')}\\n\\n"
                    "The function appears to be part of a Spring Boot microservice. "
                    "It validates input, persists state, and emits a Kafka event. "
                    "Potential concerns: idempotency, message ordering, and failure handling "
                    "for the Kafka send (fire-and-forget pattern).\\n",
    })

from starlette.staticfiles import StaticFiles

# Mount static files at root (must come after all API routes are registered)
app.mount("/", StaticFiles(directory=str(WEB), html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
