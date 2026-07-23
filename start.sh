#!/bin/bash
#Linux平台启动入口

# clear

if ! command -v uv >/dev/null 2>&1; then
 echo "uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
 exit 1
fi

if [ ! -d ".venv" ]; then
    uv sync
fi

sync_status=$?
if (($sync_status)); then
 exit $sync_status
fi

is_restart=1

while (($is_restart))
do
 stty echo
 uv run python main.py
 is_restart=$?
done
stty echo
