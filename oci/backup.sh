#!/usr/bin/env bash
# free-arm 状态备份（L1）：Orca/Agent 状态 + 密钥 + 系统配置 + 三个 Postgres 库。
# 用法：oci/backup.sh [输出目录] [--keep N]      （默认 ~/backups，保留 5 份；也认 BACKUP_KEEP）
# 产出：<输出目录>/free-arm-<UTC时间>.tar.zst（未加密；无 zstd 时退化为 .tar.gz）；给了 BACKUP_PASSPHRASE_FILE 则再 gpg 加密成 .gpg
# 排除：可重新下载的运行时/产物/插件/日志（~/.config/orca 下 2 GB+ 的部分），否则单份超 5 GB。
# 说明：SQLite 是拷副本不是热备份一致性快照；要严格一致就先停 Orca。
set -uo pipefail

OUT=$HOME/backups
KEEP=${BACKUP_KEEP:-5}
while [[ $# -gt 0 ]]; do
    case $1 in
        --keep) KEEP=${2:?--keep 需要一个数字}; shift 2 ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) OUT=$1; shift ;;
    esac
done
[[ $KEEP =~ ^[0-9]+$ && $KEEP -ge 1 ]] || { echo "错误：--keep 必须是 >=1 的整数" >&2; exit 2; }
TS=$(date -u +%Y%m%dT%H%M%SZ)
WORK=$(mktemp -d)
STAGE="$WORK/free-arm-$TS"
mkdir -p "$STAGE" "$OUT"

die() { printf '错误：%s\n' "$*" >&2; rm -rf "$WORK"; exit 1; }

# 1) Orca / Agent 状态：排除可重建的运行时与巨大产物
if [[ -d $HOME/.config/orca ]]; then
    mkdir -p "$STAGE/orca"
    tar -C "$HOME/.config" -cf - \
        --exclude='orca/agent-runtimes' \
        --exclude='orca/orcad-artifacts' \
        --exclude='orca/plugins-data' \
        --exclude='orca/logs' \
        --exclude='orca/cache' \
        --exclude='*.log' \
        orca | tar -C "$STAGE" -xf -
fi

# 2) 密钥与私有配置（靠加密层保护）
for d in .ssh .oci .gnupg .config/magpie .config/115-util .config/gh; do
    [[ -e $HOME/$d ]] && { mkdir -p "$STAGE/$(dirname "$d")"; cp -a "$HOME/$d" "$STAGE/$d" 2>/dev/null; }
done

# 3) 系统侧：自定义 unit、cron、网络与挂载、portal 配置
mkdir -p "$STAGE/system"
cp -a /etc/fstab /etc/hosts /etc/hostname /etc/ssh/sshd_config "$STAGE/system/" 2>/dev/null
systemctl list-unit-files --no-pager 2>/dev/null > "$STAGE/system/unit-files.txt"
systemctl list-timers --all --no-pager 2>/dev/null > "$STAGE/system/timers.txt"
crontab -l 2>/dev/null > "$STAGE/system/crontab.txt"
for u in agentsview caddy dashboard-serve komari-agent t7-backup; do
    for ext in service timer; do
        p="/etc/systemd/system/$u.$ext"
        [[ -f $p ]] && cp -a "$p" "$STAGE/system/"
    done
done
[[ -d $HOME/portal/config ]] && { mkdir -p "$STAGE/portal"; cp -a "$HOME/portal/config" "$STAGE/portal/" 2>/dev/null; }

# 4) 数据库：容器内 pg_dump -Fc（与 t7-backup 同一套做法）
mkdir -p "$STAGE/db"
for c in $(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null | awk -F'\t' '$2 ~ /postgres/ {print $1}'); do
    [[ $c == yorches-spec006-t7* ]] && continue
    # 容器的端口/监听地址是自定义的（yorches-tests 55452、spec006-t8 55442），从 PID1 的 cmdline 里读，
    # 有 -p 就带上（unix socket 文件名含端口，不传就会连不上）；POSTGRES_USER 不一定有，兜底 postgres
    port=$(docker exec "$c" sh -c "tr '\\0' ' ' </proc/1/cmdline" 2>/dev/null | sed -n 's/.*-c port=\([0-9][0-9]*\).*/\1/p' | head -1)
    if docker exec -u postgres "$c" sh -c "pg_dump -Fc ${port:+-p $port} -d \"\$POSTGRES_DB\" -U \"\${POSTGRES_USER:-postgres}\"" > "$STAGE/db/$c.dump" 2>"$WORK/$c.err"; then
        printf '  db ok: %s %s\n' "$c" "$(du -h "$STAGE/db/$c.dump" | cut -f1)"
    else
        printf '  db FAIL: %s（%s）\n' "$c" "$(tail -1 "$WORK/$c.err")"
        rm -f "$STAGE/db/$c.dump"
    fi
done

# 5) 打包：zstd（多线程）优先，其次 pigz，最后 gzip —— 3.5 GB 的 session journal 用 gzip 太慢
if command -v zstd >/dev/null; then
    ARCHIVE="$OUT/free-arm-$TS.tar.zst"; COMP="zstd -T0 -3 -q"
elif command -v pigz >/dev/null; then
    ARCHIVE="$OUT/free-arm-$TS.tar.gz"; COMP="pigz -p $(nproc)"
else
    ARCHIVE="$OUT/free-arm-$TS.tar.gz"; COMP="gzip -1"
fi
tar -C "$WORK" -I "$COMP" -cf "$ARCHIVE" "free-arm-$TS" || die "打包失败"
[[ -s $ARCHIVE ]] || die "产出为空"

# 6) 可选加密
if [[ -n ${BACKUP_PASSPHRASE_FILE:-} ]]; then
    [[ -r $BACKUP_PASSPHRASE_FILE ]] || die "读不到 BACKUP_PASSPHRASE_FILE"
    gpg --batch --yes --symmetric --cipher-algo AES256 --compress-algo none \
        --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
        --output "$ARCHIVE.gpg" "$ARCHIVE" || die "gpg 加密失败"
    rm -f "$ARCHIVE"
    ARCHIVE="$ARCHIVE.gpg"
fi

rm -rf "$WORK"

# 7) 保留最近 KEEP 份：只删本脚本命名的产物，按修改时间倒序
mapfile -t olds < <(find "$OUT" -maxdepth 1 -type f -name 'free-arm-*.tar.*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | cut -d' ' -f2-)
if (( ${#olds[@]} > KEEP )); then
    for f in "${olds[@]:KEEP}"; do
        rm -f -- "$f" && printf '  清理旧备份：%s\n' "$(basename "$f")"
    done
fi

printf '备份完成：%s（%s，目录内保留 %s 份）\n' "$ARCHIVE" "$(du -h "$ARCHIVE" | cut -f1)" "$KEEP"
