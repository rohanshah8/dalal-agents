#!/usr/bin/env bash
# Assemble the Hugging Face Gradio Space tree into $1 (default /tmp/space). Used by CI and for local checks.
set -euo pipefail
OUT="${1:-/tmp/space}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$OUT" && mkdir -p "$OUT"
cp -r "$ROOT/src" "$OUT/src"
mkdir -p "$OUT/app" && cp "$ROOT"/app/{core.py,charts.py,gradio_app.py} "$OUT/app/"
cp "$ROOT/app/space_app.py" "$OUT/app.py"
cp "$ROOT/app/space_requirements.txt" "$OUT/requirements.txt"
cp "$ROOT/app/SPACE_README.md" "$OUT/README.md"
cp "$ROOT/LICENSE" "$OUT/"
find "$OUT" -name "__pycache__" -prune -exec rm -rf {} + -o -name "*.egg-info" -prune -exec rm -rf {} +
echo "Space tree ready in $OUT"
