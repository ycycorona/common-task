#!/usr/bin/env bash
# Oracle Cloud 会话登录（D 方案：浏览器 token，不留长期 API key）。
#
#   oci/login.sh                 会话有效就打印到期时间；否则启动授权并打印登录链接（后台等回调，立即返回）
#   oci/login.sh '<回调地址>'     把浏览器地址栏里 http://localhost:8181/#security_token=... 整条投递给本机授权进程
#
# 环境变量：OCI_REGION（默认 eu-amsterdam-1）、OCI_PROFILE（默认 session）。
# 背景：CLI 取 token 的方式是浏览器登录后重定向到跑 CLI 那台机器的 http://localhost:8181，
#      所以要么开 ssh -L 8181:127.0.0.1:8181，要么用第二种用法把回调粘回来（不改端口，端口是 CLI 写死的）。
set -uo pipefail

REGION=${OCI_REGION:-eu-amsterdam-1}
PROFILE=${OCI_PROFILE:-session}
PORT=8181
LOG=${TMPDIR:-/tmp}/oci-login.log
export OCI_CLI_AUTH=security_token
export OCI_CLI_PROFILE=$PROFILE

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
# --local 只看到期时间，验不出签名错配；两个都过才算真可用
valid() { oci session validate --local >/dev/null 2>&1 && oci iam region list >/dev/null 2>&1; }

deliver() {
    local raw=$1 tok
    tok=$(printf '%s' "$raw" | sed -n 's/.*security_token=\([^&[:space:]]*\).*/\1/p')
    [[ -n $tok ]] || die "参数里没有 security_token=，要整条粘贴浏览器地址栏的 http://localhost:$PORT/#security_token=..."
    ss -ltn 2>/dev/null | grep -q ":$PORT " || die "本机 :$PORT 没有监听，授权进程已不在；重跑 oci/login.sh 拿新链接"
    curl -fsS --max-time 15 "http://127.0.0.1:$PORT/token?security_token=$tok" >/dev/null \
        || die "投递回调失败：curl 没拿到 200"
    for _ in 1 2 3 4 5 6; do sleep 1; valid && break; done
    valid || die "回调已送达但会话仍无效，看 $LOG"
    # --local 只看得到到期时间，验不出“token 属于另一对密钥”，所以再打一次真接口
    if ! oci iam region list >/dev/null 2>&1; then
        die "token 有到期时间但签名对不上（401）：粘的多半是**旧链接**的回调。重跑 oci/login.sh 拿新链接，登录后粘新的那条"
    fi
    printf '登录成功，%s\n' "$(oci session validate --local)"
}

if [[ $# -gt 0 ]]; then
    deliver "$1"
    exit $?
fi

if valid; then
    oci session validate --local
    exit 0
fi

if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
    if [[ -s $LOG ]]; then
        url=$(grep -o 'https://[^[:space:]]*action=login[^[:space:]]*' "$LOG" | head -1)
        [[ -n $url ]] && { printf '已有一次授权在等回调，直接用这条链接：\n%s\n\n' "$url"; exit 0; }
    fi
    die "本机 :$PORT 被占用但不是本脚本起的：pkill -f 'oci session authenticate' 后再试"
fi

: >"$LOG"
nohup oci session authenticate --region "$REGION" --profile-name "$PROFILE" >"$LOG" 2>&1 &
bgpid=$!

url=""
for _ in $(seq 1 20); do
    url=$(grep -o 'https://[^[:space:]]*action=login[^[:space:]]*' "$LOG" | head -1)
    [[ -n $url ]] && break
    kill -0 "$bgpid" 2>/dev/null || { printf '授权进程已退出：\n' >&2; cat "$LOG" >&2; exit 1; }
    sleep 1
done
[[ -n $url ]] || { cat "$LOG" >&2; die "20 秒内没拿到登录链接"; }

cat <<EOF
在浏览器打开这条链接登录（每次链接都不同，别用旧标签页里那条）：
$url

登录完成后的两种走法：
  A. 已经开着隧道（在 Mac 上 ssh -L $PORT:127.0.0.1:$PORT free-arm）：会自动完成，再跑一次 oci/login.sh 看到期时间。
  B. 没隧道、页面停在 "This site can't be reached"：把地址栏里那条
     http://localhost:$PORT/#security_token=...
     整条复制，然后跑
     oci/login.sh 'http://localhost:$PORT/#security_token=...'

授权进程 pid=$bgpid，日志 $LOG。它不会自己超时；放弃时 pkill -f 'oci session authenticate'。
EOF
