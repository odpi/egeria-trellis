#!/bin/bash
# Update all code analysis data
# This script runs all analysis tools to regenerate cached data

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=========================================="
echo "Updating All Code Analysis Data"
echo "=========================================="
echo ""

# Activate virtual environment
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
else
    echo "Error: Virtual environment not found at $PROJECT_DIR/.venv"
    exit 1
fi

# Change to project directory
cd "$PROJECT_DIR"

echo "Step 1: Generating Enhanced Metrics..."
echo "--------------------------------------"
python scripts/generate_enhanced_metrics.py
if [ $? -eq 0 ]; then
    echo "✓ Enhanced metrics generated successfully"
else
    echo "✗ Failed to generate enhanced metrics"
    exit 1
fi
echo ""

echo "Step 2: Generating Enhanced Relationships..."
echo "--------------------------------------"
python scripts/generate_enhanced_relationships.py
if [ $? -eq 0 ]; then
    echo "✓ Enhanced relationships generated successfully"
else
    echo "✗ Failed to generate enhanced relationships"
    exit 1
fi
echo ""

# (Step 3, scripts/generate_report_specs.py, was removed 2026-11-11 (A-P3): it imported dead
# pre-6.x pyegeria paths and its output, data/cache/report_specs.json, had no reader.)

echo "=========================================="
echo "All Analysis Data Updated Successfully!"
echo "=========================================="
echo ""
echo "Generated files:"
echo "  - data/cache/enhanced_metrics.json"
echo "  - data/cache/enhanced_relationships.json"
echo ""
echo "You can now query the enhanced data using:"
echo "  - Enhanced analytics queries (metrics, complexity, maintainability)"
echo "  - Enhanced relationship queries (imports, calls, inheritance)"
echo ""