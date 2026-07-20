#!/usr/bin/env bash
set -e
export CONFIG_PATH="${CONFIG_PATH:-/data/options.json}"
export SHADOW_CONFIG_PATH="${SHADOW_CONFIG_PATH:-/data/dashsnap.json}"
# System chromium ships H264 (Debian build) for live camera streams.
export CHROMIUM_PATH="${CHROMIUM_PATH:-/usr/bin/chromium}"
exec python3 /record.py
