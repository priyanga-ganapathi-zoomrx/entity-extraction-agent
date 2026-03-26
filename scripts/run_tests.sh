#!/bin/bash
# Run unit tests (fast)

set -e

echo "================================"
echo "Running Unit Tests (Fast)"
echo "================================"
echo ""

# Activate virtual environment
source .venv/bin/activate

# Run unit tests with coverage
python -m pytest tests/unit \
    -m unit \
    --cov=src \
    --cov-report=term-missing \
    --cov-report=html \
    -v

echo ""
echo "================================"
echo "Unit Tests Complete!"
echo "================================"
echo ""
echo "Coverage report saved to: htmlcov/index.html"
echo ""
