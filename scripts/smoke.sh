#!/usr/bin/env bash
# 端点 smoke test：枚举 OpenAPI 中所有 GET 路由，按 0..4xx 通过 / 5xx 失败分类。
# 用法: ./scripts/smoke.sh          （默认 api 容器内 8000）
#       BASE=http://host:port ./scripts/smoke.sh

set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
COMPOSE_FILE="$(cd "$(dirname "$0")/.." && pwd)/docker-compose.yml"
COMPOSE="docker compose -f $COMPOSE_FILE"
SVC="${SVC:-api}"

echo "--- Smoke @ $BASE (via service=$SVC) ---"
$COMPOSE exec -T "$SVC" python - "$BASE" <<'PY'
import json, sys, urllib.request, urllib.error

base = sys.argv[1]
with urllib.request.urlopen(base + "/openapi.json", timeout=5) as r:
    data = json.load(r)
get_paths = sorted(p for p in data["paths"] if "get" in data["paths"][p])

def code(p):
    try:
        with urllib.request.urlopen(base + p, timeout=8) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0

fail, ok200, ok4xx = 0, 0, 0
for p in get_paths:
    c = code(p)
    if 200 <= c < 300:
        ok200 += 1
    elif 400 <= c < 500:
        ok4xx += 1
    else:
        fail += 1
        print(f"  FAIL [{c}] {p}")

print(f"GET endpoints: {len(get_paths)}")
print(f"  200 OK:    {ok200}")
print(f"  4xx (param/tolerated): {ok4xx}")
print(f"  FAIL:      {fail}")
sys.exit(1 if fail else 0)
PY
