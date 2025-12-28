#!/bin/bash

# Run all tests for ytdown project
# Usage: ./run_tests.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
# Priority: /home/pi/venv (production) > local .venv (development)
if [ -d "/home/pi/venv" ]; then
    source /home/pi/venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Add project root to PYTHONPATH for imports
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

echo "========================================"
echo "Running ytdown tests"
echo "========================================"
echo ""

FAILED=0
PASSED=0

run_test() {
    local test_file="$1"
    local test_name=$(basename "$test_file")

    echo "----------------------------------------"
    echo "Running: $test_name"
    echo "----------------------------------------"

    if python3 "$test_file"; then
        echo "[PASS] $test_name"
        PASSED=$((PASSED + 1))
    else
        echo "[FAIL] $test_name"
        FAILED=$((FAILED + 1))
    fi
    echo ""
}

# Run standalone tests first (no dependencies)
run_test "tests/test_security_standalone.py"
run_test "tests/test_json_simple.py"

# Run tests that require main app imports
run_test "tests/test_security.py"
run_test "tests/test_json_persistence.py"

echo "========================================"
echo "Test Summary"
echo "========================================"
echo "Passed: $PASSED"
echo "Failed: $FAILED"
echo ""

if [ $FAILED -gt 0 ]; then
    echo "Some tests failed!"
    exit 1
else
    echo "All tests passed!"
    exit 0
fi
