#!/usr/bin/env sh
# Constellation container entrypoint (lives in docker/).
# Seeds the two demo graphs on first run (the named volume persists them),
# then starts the FastAPI server, which serves the pre-built UI from web/dist/.
set -e

if [ ! -f output/graph.json ]; then
  echo "-> Generating Spring Boot demo graph..."
  python -m engine.constellation \
    tests/repos/order-service \
    tests/repos/fulfillment-service \
    tests/repos/notification-service \
    --output output/graph.json
fi

if [ ! -f output/graph-java-ee.json ]; then
  echo "-> Generating Java EE demo graph..."
  python -m engine.constellation \
    tests/repos/java-ee-order-service \
    tests/repos/java-ee-fulfillment-service \
    tests/repos/java-ee-notification-service \
    --output output/graph-java-ee.json
fi

echo "-> Starting Constellation on port ${CONSTELLATION_PORT:-8765}..."
exec python -m uvicorn server:app --host 0.0.0.0 --port "${CONSTELLATION_PORT:-8765}"
