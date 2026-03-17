#!/bin/bash
# Run integration tests (slower)

set -e

echo "================================"
echo "Running Integration Tests"
echo "================================"
echo ""

# Activate virtual environment
source .venv/bin/activate

# Run integration tests
python -m pytest tests/integration \
    -m integration \
    --integration \
    -v

echo ""
echo "================================"
echo "Integration Tests Complete!"
echo "================================"
