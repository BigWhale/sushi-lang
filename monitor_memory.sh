#!/bin/bash
# Memory monitoring script for RAII leak test

if [ -z "$1" ]; then
    echo "Usage: $0 <program>"
    echo "Example: $0 ./test_raii_memory_leak"
    exit 1
fi

PROGRAM="$1"

echo "Starting memory monitoring for: $PROGRAM"
echo "Memory usage (RSS in KB) will be displayed every 0.1 seconds"
echo "================================================"

# Run the program in background
"$PROGRAM" &
PID=$!

# Track initial and max memory
INITIAL_MEM=0
MAX_MEM=0
SAMPLE_COUNT=0

# Monitor memory while process is running
while kill -0 $PID 2>/dev/null; do
    # Get RSS (Resident Set Size) in kilobytes
    RSS=$(ps -o rss= -p $PID 2>/dev/null | tr -d ' ')

    if [ -n "$RSS" ]; then
        # Track initial memory (first non-zero reading)
        if [ $INITIAL_MEM -eq 0 ] && [ $RSS -gt 0 ]; then
            INITIAL_MEM=$RSS
        fi

        # Track maximum memory
        if [ $RSS -gt $MAX_MEM ]; then
            MAX_MEM=$RSS
        fi

        # Print current memory
        echo "RSS: ${RSS} KB"

        SAMPLE_COUNT=$((SAMPLE_COUNT + 1))
    fi

    sleep 0.1
done

# Wait for process to complete
wait $PID
EXIT_CODE=$?

# Print summary
echo "================================================"
echo "Memory monitoring complete!"
echo "Exit code: $EXIT_CODE"
echo "Initial memory: ${INITIAL_MEM} KB ($(awk "BEGIN {printf \"%.2f\", $INITIAL_MEM/1024}") MB)"
echo "Maximum memory: ${MAX_MEM} KB ($(awk "BEGIN {printf \"%.2f\", $MAX_MEM/1024}") MB)"

if [ $INITIAL_MEM -gt 0 ]; then
    GROWTH_KB=$((MAX_MEM - INITIAL_MEM))
    GROWTH_PERCENT=$(awk "BEGIN {printf \"%.1f\", ($MAX_MEM - $INITIAL_MEM) * 100.0 / $INITIAL_MEM}")
    echo "Memory growth: ${GROWTH_KB} KB (+${GROWTH_PERCENT}%)"

    # Check if memory growth is reasonable (less than 20% suggests RAII is working)
    if [ $(echo "$GROWTH_PERCENT < 20" | bc -l 2>/dev/null || echo 1) -eq 1 ]; then
        echo "✓ RAII appears to be working! Memory stayed relatively stable."
    else
        echo "⚠ Significant memory growth detected - possible leak!"
    fi
else
    echo "Could not measure memory growth"
fi

echo "Samples collected: $SAMPLE_COUNT"
