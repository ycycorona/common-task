#!/usr/bin/env bash
set -uo pipefail

# 检查文档规范里能机器判定的两条：相对链接可达、docs/ 与 archives/ 的文档有且只有一行
# Last Validated（docs/agents/documentation-conventions.md）。
# 用法: ./devtools/check_docs.sh [仓库根目录]
# 只报告不判失败的例外写在 GRANDFATHERED：改过对应文档后就把它删掉，让它重新受检。

root=${1:-$(cd "$(dirname "$0")/.." && pwd)}
fail=0

GRANDFATHERED=(
  "docs/MagPie Web 管理界面部署记录.md"
  "docs/macOS SSH Key 全流程.md"
  "archives/荷兰超市宣传册下载.md"
  "archives/irm-avlabo-starter.md"
  "docs/agents/documentation-conventions.md"
)

error() { echo "FAIL $*"; fail=1; }

# 1. 相对链接必须可达
while IFS= read -r doc; do
  dir="$root/$(dirname "$doc")"
  while IFS= read -r raw; do
    link=${raw#](}
    link=${link%)}
    case "$link" in
      http://*|https://*|mailto:*|\#*|"") continue ;;
    esac
    path=${link%%#*}
    [ -e "$dir/$path" ] || error "$doc: 链接不可达 -> $link"
  done < <(grep -o '](\([^)]*\))' "$root/$doc" 2>/dev/null)
done < <(cd "$root" && find . \( -name .git -o -name .venv -o -name .pytest_cache -o -name node_modules \) -prune -o -name '*.md' -print | sed 's|^\./||')

# 2. docs/ 与 archives/ 的文档要有且只有一行 Last Validated
for doc in "$root"/docs/*.md "$root"/docs/agents/*.md "$root"/archives/*.md; do
  [ -e "$doc" ] || continue
  rel=${doc#"$root"/}
  count=$(grep -c '^Last Validated:' "$doc" || true)
  if [ "$count" = 1 ]; then
    continue
  fi
  for g in "${GRANDFATHERED[@]}"; do
    [ "$rel" = "$g" ] && continue 2
  done
  error "$rel: Last Validated 有 $count 行，应为 1 行"
done

# 3. 例外清单里的文件消失或已补齐时提醒清理
for g in "${GRANDFATHERED[@]}"; do
  [ -e "$root/$g" ] || echo "note $g 已不存在，请从 GRANDFATHERED 移除"
done

[ "$fail" = 0 ] && echo "ok 文档检查通过"
exit "$fail"
