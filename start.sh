#!/usr/bin/env bash
# Boots llama-server and the FastAPI app together. Ctrl-C stops both.
set -euo pipefail

cd "$(dirname "$0")"

MODEL="$(python3 -c 'import json;print(json.load(open("config.json"))["model_path"])')"
N_CTX="$(python3 -c 'import json;print(json.load(open("config.json"))["n_ctx"])')"
LLAMA_PORT="$(python3 -c 'import json;print(json.load(open("config.json"))["llama_port"])')"
APP_PORT="$(python3 -c 'import json;print(json.load(open("config.json"))["app_port"])')"

if [ ! -f "$MODEL" ]; then
  echo "Model not found: $MODEL"
  echo "Download it with:"
  echo "  ./.venv/bin/huggingface-cli download Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf --local-dir ./models"
  exit 1
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "→ starting llama-server on :$LLAMA_PORT (ctx $N_CTX)"
# -ngl 99      : all layers on the M3 GPU via Metal
# -fa on       : flash attention, required for a quantized V cache
# q8_0 KV      : halves KV-cache memory; the cache costs ~144 KB/token at f16
# --jinja ...  : splits Qwen3's <think> output into a separate reasoning_content field
llama-server \
  -m "$MODEL" \
  --host 127.0.0.1 --port "$LLAMA_PORT" \
  -c "$N_CTX" -ngl 99 \
  -fa on --cache-type-k q8_0 --cache-type-v q8_0 \
  --jinja --reasoning-format deepseek \
  > llama-server.log 2>&1 &

echo "→ waiting for model to load…"
for i in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:$LLAMA_PORT/health" >/dev/null 2>&1; then
    echo "✓ model ready"
    break
  fi
  sleep 1
done

echo "→ starting app on http://127.0.0.1:$APP_PORT"
./.venv/bin/uvicorn server.main:app --host 127.0.0.1 --port "$APP_PORT"
