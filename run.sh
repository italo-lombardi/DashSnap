#!/usr/bin/env bash
set -e
export CONFIG_PATH="${CONFIG_PATH:-/data/options.json}"
export SHADOW_CONFIG_PATH="${SHADOW_CONFIG_PATH:-/data/dashsnap.json}"
exec python3 /record.py
