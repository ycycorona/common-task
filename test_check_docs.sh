#!/usr/bin/env bash
set -uo pipefail

# check_docs.sh 的自检：在临时目录里造正反两套文档，覆盖链接、Last Validated 与例外清单。
# 用法: bash test_check_docs.sh

here=$(cd "$(dirname "$0")" && pwd)
script="$here/check_docs.sh"
work=$(mktemp -d "${TMPDIR:-/tmp}/check-docs-test.XXXXXX")
trap 'rm -rf "$work"' EXIT

fail=0
check() { # check <描述> <期望退出码> <根目录>
  local desc=$1 want=$2 root=$3 out got
  out=$(bash "$script" "$root" 2>&1)
  got=$?
  if [ "$got" = "$want" ]; then
    echo "ok   $desc"
  else
    echo "FAIL $desc (exit=$got, 期望 $want)"
    echo "$out" | sed 's/^/     /'
    fail=1
  fi
}

# 合规的一套
good="$work/good"
mkdir -p "$good/docs/agents" "$good/archives"
printf '# 记录\n\nLast Validated: 2026-11-02\n\n看 [规范](docs/agents/documentation-conventions.md) 与 [外链](https://example.com) 与 [锚点](#x)。\n' > "$good/README.md"
printf '# 规范\n\n无 Last Validated 也就够了，它在例外清单里不受检。\n' > "$good/docs/agents/documentation-conventions.md"
printf '# 记录\n\nLast Validated: 2026-11-02\n\n配套脚本见 [a.py](../a.py)。\n' > "$good/docs/note.md"
printf 'print(1)\n' > "$good/a.py"
printf '# 归档\n\nLast Validated: 2026-11-02\n' > "$good/archives/old.md"
check "合规文档通过" 0 "$good"

# 断链
badlink="$work/badlink"
mkdir -p "$badlink/docs"
printf '# 记录\n\nLast Validated: 2026-11-02\n\n看 [缺失](../missing.md)。\n' > "$badlink/docs/note.md"
check "断链失败" 1 "$badlink"

# 缺 Last Validated
nolv="$work/nolv"
mkdir -p "$nolv/docs"
printf '# 记录\n\n只有正文。\n' > "$nolv/docs/note.md"
check "缺 Last Validated 失败" 1 "$nolv"

# 两行 Last Validated
duplv="$work/duplv"
mkdir -p "$duplv/docs"
printf '# 记录\n\nLast Validated: 2026-11-02\n\nLast Validated: 2026-11-03\n' > "$duplv/docs/note.md"
check "重复 Last Validated 失败" 1 "$duplv"

# 真实仓库应当当前就是合规的
check "本仓库通过" 0 "$here"

if [ "$fail" = 0 ]; then echo "全部通过"; else echo "有失败项"; fi
exit "$fail"
