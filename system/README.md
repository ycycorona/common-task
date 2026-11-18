# System

本机（macOS）系统层脚本：电源/睡眠、合盖、`launchd` 常驻任务这类**不属于某个具体业务**的小工具。
每个脚本独立、可单独卸载，不引入第三方依赖；需要常驻的，plist 与本目录脚本放在一起。

判据：改成“日常用机行为”的脚本放这里；只是某次性的整理、批处理放 `archives/` 或对应业务目录。

## ac-lidsleep.sh

插电时禁止合盖睡眠（`pmset -a disablesleep 1`），拔电时放开（`0`）；合盖瞬间让内屏立刻休眠，
不等 5 分钟的空闲显示器计时器。另一个方向：**插电合盖运行着、随后拔电**时，脚本会补一次
`pmset sleepnow`，因为那次合盖事件已经过去了，光放开开关不会让机器睡；这条用来把行为交回
macOS 正常的电池策略。每 10 秒由 `local.ac-lidsleep.plist` 跑一次。

背景：macOS 的合盖睡眠是强制睡眠，`caffeinate` / Amphetamine 的电源断言只能挡空闲睡眠，挡不住合盖；
真正能挡的只有 `disablesleep` 这个全局开关，而且系统不提供“仅插电生效”的写法，所以用轮询兜。

依赖：能免密执行这两条命令的 sudo 规则，例如 Amphetamine Power Protect 装下的
`/etc/sudoers.d/amphetamine_PowerProtect`：

```text
Cmnd_Alias PMSET_AMPHETAMINE= /usr/bin/pmset -a disablesleep 1, /usr/bin/pmset -a disablesleep 0
%admin ALL=(ALL) NOPASSWD: PMSET_AMPHETAMINE
```

没有这条规则时脚本会在日志里写 `FAILED` 并以非零退出，不会静默放过。

`pmset sleepnow` 也需要权限：脚本先按普通用户调用，失败再试 `sudo -n`。若两种都失败，日志会写
`sleepnow FAILED`。要让它可靠，追加一条规则（需管理员密码一次）：

```bash
echo '%admin ALL=(ALL) NOPASSWD: /usr/bin/pmset sleepnow' | sudo tee /etc/sudoers.d/pmset-sleepnow
sudo chmod 440 /etc/sudoers.d/pmset-sleepnow
```

### 安装

```bash
chmod +x ~/Code/common-task/system/ac-lidsleep.sh
cp ~/Code/common-task/system/local.ac-lidsleep.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/local.ac-lidsleep.plist
tail -f ~/Library/Logs/ac-lidsleep.log          # 每 10 秒一轮，只在状态变化时写日志
```

卸载：`launchctl unload ~/Library/LaunchAgents/local.ac-lidsleep.plist`，
再视需要 `sudo pmset -a disablesleep 0`。

plist 里的脚本路径是绝对路径，换机器/换目录要同步改。

### 自检

```bash
bash ~/Code/common-task/system/test_ac_lidsleep.sh
```

只测纯函数（AC/battery 判定、ac→battery+合盖 才补睡），不碰系统状态。

### 已知限制

- 有外接屏并合盖时，脚本会把外接屏也灭一次（每 10 秒轮询里无法低成本可靠地判断是否外接屏，故未做区分）。
- 依赖 10 秒轮询，插拔电源后最长约 10 秒内状态才切换；这期间合盖仍会睡。
- “插电合盖 → 拔电 → 立刻睡”只在确实检测到 ac → battery 变化时触发；脚本首次运行、或状态文件丢失时不补睡。
- 判断合盖靠 `ioreg -k AppleClamshellState`，属于未公开接口，macOS 大版本升级后需复核。
