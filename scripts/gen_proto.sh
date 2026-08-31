#!/usr/bin/env bash
# Regenerate Python bindings from the amp's protobuf schemas.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf src/lt25_mcp/_generated
mkdir -p src/lt25_mcp/_generated
uv run python -m grpc_tools.protoc \
  -Iproto \
  --python_out=src/lt25_mcp/_generated \
  --pyi_out=src/lt25_mcp/_generated \
  proto/*.proto
touch src/lt25_mcp/_generated/__init__.py
# Generated modules import each other by bare name, so the package directory
# must be on sys.path when they load. messages.py handles that.
echo "generated $(ls src/lt25_mcp/_generated/*_pb2.py | wc -l | tr -d ' ') modules"
