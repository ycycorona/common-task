# macOS SSH Key 全流程（生成 -> 设置私钥口令 -> agent+Keychain -> 安装到服务器）

把下面命令里的 USER 和 HOST 换成你的，例如：
USER=ubuntu
HOST=1.2.3.4
或 HOST=myvps.example.com

------------------------------------------------------------

1) 准备：创建 ~/.ssh 并设置权限

mkdir -p ~/.ssh
chmod 700 ~/.ssh

------------------------------------------------------------

2) 生成钥匙对（ed25519，推荐）

说明：
- 这一步会提示你输入 passphrase（私钥口令），强烈建议不要留空
- 生成后会得到：
  私钥：~/.ssh/id_ed25519
  公钥：~/.ssh/id_ed25519.pub

命令：

ssh-keygen -t ed25519 -a 64 -f ~/.ssh/id_ed25519 -C "macbook-$(whoami)"

------------------------------------------------------------

3) 如果你已经有私钥但想设置/修改 passphrase（可跳过）

ssh-keygen -p -f ~/.ssh/id_ed25519

------------------------------------------------------------

4) 使用 ssh-agent + Keychain 管理私钥口令（减少/免输 passphrase）

4.1 把 key 加入 agent，并把 passphrase 存进 Keychain（一次性）

ssh-add --apple-use-keychain ~/.ssh/id_ed25519

4.2 配置 ~/.ssh/config，让以后自动把 key 加入 agent，并使用 Keychain

cat >> ~/.ssh/config <<'EOF'
Host *
  AddKeysToAgent yes
  UseKeychain yes
  IdentityFile ~/.ssh/id_ed25519
EOF

chmod 600 ~/.ssh/config

4.3 验证：看 agent 里是否已经加载了 key

ssh-add -l

------------------------------------------------------------

5) 把公钥安装到服务器（写入 ~/.ssh/authorized_keys）

注意：
- 第一次会要求你输入服务器账号密码（这是“最后一次”）
- 安装成功后，以后 ssh 将优先用密钥登录

方案A：如果你的系统里有 ssh-copy-id（有就用）

ssh-copy-id -i ~/.ssh/id_ed25519.pub USER@HOST

方案B：macOS 默认可能没有 ssh-copy-id（通用替代命令，推荐一定能用）

cat ~/.ssh/id_ed25519.pub | ssh USER@HOST \
'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'

------------------------------------------------------------

6) 测试登录

ssh USER@HOST

如果你想明确指定用哪把私钥：

ssh -i ~/.ssh/id_ed25519 USER@HOST

------------------------------------------------------------

7) 常用排错/管理命令

7.1 看 ssh 详细过程（确认是否用了你的 key）

ssh -vv USER@HOST

7.2 查看 agent 中已加载的 key

ssh-add -l

7.3 清空 agent（临时“忘记缓存”）

ssh-add -D

------------------------------------------------------------

8) 安全提醒（很重要）

- 不要在不可信服务器上使用 ssh -A（Agent Forwarding）
  否则远端可能“借用你的 agent”去访问别的机器（虽然拿不到私钥文件）
- 如果你要把 key 用在公共/临时服务器，建议单独生成一把专用 key
  不要用你最常用的“万能 key”到处复制

（可选：生成专用 key 示例）

ssh-keygen -t ed25519 -a 64 -f ~/.ssh/id_ed25519_publicservers -C "public-servers"
ssh-add --apple-use-keychain ~/.ssh/id_ed25519_publicservers

然后在 ~/.ssh/config 里给特定服务器指定专用 key（示例）

Host myvps
  HostName your.server.example.com
  User USER
  IdentityFile ~/.ssh/id_ed25519_publicservers

之后直接：

ssh myvps
