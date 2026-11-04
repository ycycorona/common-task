#!/usr/bin/env bash
set -uo pipefail

# remote-dir-sync.sh 的自检：用一个把远程命令在本地执行的假 ssh，覆盖成功与失败两条路径。
# 用法: bash test_remote_dir_sync.sh

here=$(cd "$(dirname "$0")" && pwd)
script="$here/remote-dir-sync.sh"
work=$(mktemp -d "${TMPDIR:-/tmp}/sync-test.XXXXXX")
trap 'rm -rf "$work"' EXIT

fail=0
check() { # check <描述> <命令...>
  local desc=$1; shift
  if "$@"; then echo "ok   $desc"; else echo "FAIL $desc"; fail=1; fi
}

mkdir -p "$work/bin"
# 假 ssh：忽略主机名，把“远程命令”在本地执行
cat > "$work/bin/ssh" <<'EOF'
#!/usr/bin/env bash
exec bash -c "$2"
EOF
chmod +x "$work/bin/ssh"
export PATH="$work/bin:$PATH"

src="$work/remote/unpacked"
mkdir -p "$src/sub"
echo new > "$src/a.txt"
echo nested > "$src/sub/b.txt"

# 1) 成功路径：旧文件被清掉，新内容到位
dest="$work/local/unpacked"
mkdir -p "$dest"
echo old > "$dest/STALE.txt"
mkdir -p "$dest/stale-dir" && echo old > "$dest/stale-dir/x"

out=$(bash "$script" fake-host "$src" "$dest" 2>&1)
check "成功时退出码为 0" test $? -eq 0
check "输出包含文件数 2" grep -q '2 个文件' <<<"$out"
check "新文件到位" test "$(cat "$dest/a.txt")" = new
check "嵌套文件到位" test "$(cat "$dest/sub/b.txt")" = nested
check "旧文件被清掉" test ! -e "$dest/STALE.txt"
check "旧目录被清掉" test ! -e "$dest/stale-dir"
check "无临时目录残留" test -z "$(find "$work/local" -maxdepth 1 -name '.sync.*')"

# 2) 失败路径：远程路径不存在时退出非零，本地目录保持原样
echo keep > "$dest/keep.txt"
bash "$script" fake-host "$work/remote/does-not-exist" "$dest" >/dev/null 2>&1
check "失败时退出码非零" test $? -ne 0
check "失败时本地未被清空" test "$(cat "$dest/keep.txt")" = keep
check "失败时无临时目录残留" test -z "$(find "$work/local" -maxdepth 1 -name '.sync.*')"

# 3) 空远程目录同样按失败处理
mkdir -p "$work/remote/empty"
bash "$script" fake-host "$work/remote/empty" "$dest" >/dev/null 2>&1
check "空远程目录退出码非零" test $? -ne 0
check "空远程目录时本地未改动" test "$(cat "$dest/keep.txt")" = keep

exit $fail
