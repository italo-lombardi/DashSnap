#!/usr/bin/env bash
set -e
export CONFIG_PATH="${CONFIG_PATH:-/data/options.json}"
exec python3 /record.py
