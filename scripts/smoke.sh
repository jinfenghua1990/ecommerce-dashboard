#!/usr/bin/env bash
# 端点 smoke test：枚举 OpenAPI 中所有 GET 路由，按 2xx / 4xx / 5xx 分类。
# RBAC 模式下先登录拿令牌再探测，否则 401 会淹没真实 5xx —— 鉴权失败单列统计。
#
# 用法: ./scripts/smoke.sh                    # api 容器内 8000
#       BASE=http://host:port ./scripts/smoke.sh
#       SMOKE_USER=admin SMOKE_PASS=xxx ./scripts/smoke.sh
#       SVC=api ./scripts/smoke.sh

set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
COMPOSE_FILE="$(cd "$(dirname "$0")/.." && pwd)/docker-compose.yml"
COMPOSE="docker compose -f $COMPOSE_FILE"
SVC="${SVC:-api}"

echo "--- Smoke @ $BASE (via service=$SVC) ---"
$COMPOSE exec -T \
  -e SMOKE_USER="${SMOKE_USER:-}" \
  -e SMOKE_PASS="${SMOKE_PASS:-}" \
  "$SVC" python - "$BASE" <<'PY'
import json, os, sys, urllib.request, urllib.error

base = sys.argv[1]

def req(path, method="GET", body=None, token=None, timeout=8):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return 0, b""

# ---- 1. 取凭证：显式传参 > 容器内 settings（RBAC 的初始管理员） ----
user = os.environ.get("SMOKE_USER") or ""
pwd = os.environ.get("SMOKE_PASS") or ""
if not user:
    try:
        from app.config import settings as s
        user, pwd = s.ADMIN_USERNAME, s.ADMIN_PASSWORD
    except Exception:
        pass

# ---- 2. 登录拿令牌（拿不到就匿名跑，鉴权失败会单列统计出来） ----
token = None
if user and pwd:
    code, raw = req("/api/v1/auth/login", "POST",
                    {"username": user, "password": pwd})
    if 200 <= code < 300:
        try:
            token = json.loads(raw or b"{}").get("accessToken")
        except Exception:
            token = None
    mode = "RBAC (已登录)" if token else "RBAC (登录失败 → 匿名)"
else:
    mode = "匿名（未配置管理员凭证）"
print(f"  模式: {mode}\n")

# ---- 3. 枚举全部 GET 路由并探测 ----
with urllib.request.urlopen(base + "/openapi.json", timeout=5) as r:
    data = json.load(r)
get_paths = sorted(p for p in data["paths"] if "get" in data["paths"][p])

fail, ok200, unauth, ok4xx = [], 0, 0, 0
for p in get_paths:
    c, _ = req(p, token=token)
    if 200 <= c < 300:
        ok200 += 1
    elif c in (401, 403):
        unauth += 1
    elif 400 <= c < 500:
        ok4xx += 1
    else:
        fail.append((c, p))

for c, p in fail:
    print(f"  FAIL [{c}] {p}")

print(f"\nGET endpoints: {len(get_paths)}")
print(f"  200 OK:        {ok200}")
print(f"  4xx (参数/容忍): {ok4xx}")
print(f"  401/403 鉴权:  {unauth}" + ("   ← 令牌无效，上面的 200 不可信" if unauth and not token else ""))
print(f"  FAIL:          {len(fail)}")
sys.exit(1 if fail else 0)
PY
