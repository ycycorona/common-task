# zed-defaults — 把 macOS 编程文件默认程序从 VS Code 换成 Zed

macOS 15.7.9 实测。目标 UTI 共 **98** 个；采样 **100** 个真实文件名，**99 个双击开 Zed**。

## 当前状态

A 层（源码）、B 层（`.sh` `.txt` `.css` `.xml` `.svg` `.csv` `.conf` `.ts` 等共享类型）全部指向 Zed；
另外用 `public.data` 兜住了无扩展名文件（`.zshrc` `.gitconfig` `.gitignore` `Dockerfile` `Gemfile` 等）。

唯一例外：`.html` / `.htm`。

最终实测见 [resolve-final.txt](resolve-final.txt)。

## 图标缓存 —— 换默认程序后图标不变的原因

- VS Code 自带 **33 个文档图标**（`c.icns` / `java.icns` / `shell.icns` …），Zed 只有 **1 个** `Document.icns`。
  换默认程序后每个文件类型都要重新绑定图标；绑定完成后不清缓存，Finder 会一直显示旧图标。
- `./zeddefaults flush` 做四件事：`lsregister -f` 重注册 Zed → 杀 `iconservicesagent`
  → 删除每用户缓存 `C/com.apple.iconservices` → 重启 Finder。
- 实测：111 个目标中 **93 个图标哈希发生变化**，图标种类从 **28 种收敛到 8 种**。
  对比数据在 [icons-before.txt](icons-before.txt) 与 [icons-after.txt](icons-after.txt)，
  可随时用 `./zeddefaults icons` 重测。

## 命令

```bash
swiftc -O -o zeddefaults zeddefaults.swift   # 构建

./zeddefaults selftest          # 纯逻辑自检（标签解析 + 过宽类型拦截），不写系统
./zeddefaults snapshot before.json
./zeddefaults apply             # 设置后会回读校验，不信任返回值
./zeddefaults flush             # 刷新 LaunchServices 与图标缓存
./zeddefaults list              # 目标 -> UTI -> 当前 handler
./zeddefaults resolve           # 目标 -> macOS 实际会用哪个程序打开
./zeddefaults revert before.json
```

## 回滚

```bash
./zeddefaults revert snapshot-before.json && ./zeddefaults flush
```

[snapshot-before.json](snapshot-before.json) 是改动前的真实基线（146 条，含 `public.data -> TextEdit`）。
该路径已实跑验证（还原 134 个 handler）。

**已知回滚缺口**：有 11 个类型在改动前没有任何 handler
（`kt` `kts` `scala` `hs` `erl` `hrl` `fish` `styl` `svelte` `astro` `env`）。
macOS 没有"删除 handler"的 API —— `LSSetDefaultRoleHandlerForContentType` 传 `NULL` 返回 `-50`，
受保护的 plist 又无法直接写，所以这 11 个回滚后仍指向 Zed。`revert` 会把它们列出来，不会假装成功。

## 已知限制

1. **`.html` / `.htm` 改不了。** `public.html` 被绑定到默认浏览器（当前 Chrome），
   `LSSetDefaultRoleHandlerForContentType` 返回 `-54` —— **连设成 Safari 也是 `-54`**，与 Zed 无关。
   只能手动：Finder 里选一个 `.html` → 显示简介 → 打开方式选 Zed → **全部更改**。
2. **`public.data` 是指向 Zed 的，这是故意的。** `.zshrc` / `.gitconfig` / `.gitignore` /
   `Dockerfile` / `Gemfile` 在 LaunchServices 眼里就是 `public.data`，这是唯一够得着它们的抓手
   （它们各自的 `dyn.*` 名字标签存在，但 Finder 对这些文件不用）。
   已实测不会外溢：`.zip` `.pdf` `.png` `.mp4` `.dmg` `.iso` `.docx` `.xlsx` `.epub` `.jar`
   全部保持原有程序 —— LaunchServices 优先采用最具体的绑定。
   副作用是无类型文件（例如 `id_rsa`）也会用 Zed 打开。
3. plist 里 `md` 的 tag 条目仍写着 VS Code。`net.daringfireball.markdown` 的 UTI 条目优先，
   实际已开 Zed，无实际影响；受保护的 plist 写不进去，清不掉。

## 改代码前先读：三个踩过的坑

- **不能用 `URL.contentTypeKey` 解析类型。** 它做内容嗅探，对 `.gitignore` / `Makefile` 返回
  `public.data`，会让全系统无类型文件都被劫持到 Zed。必须用
  `UTTypeCreatePreferredIdentifierForTag(kUTTagClassFilenameExtension, ...)`，
  并且无扩展名文件要传"去掉前导点后的整个名字"（`.gitignore` → `gitignore`）。
- **对 `dyn.*` UTI 设 handler，LaunchServices 会自动归一化成 `public.filename-extension` tag 条目**，
  也就是 Finder"全部更改"写的那种。所以同一个调用既能改 `public.python-script` 这类真实 UTI，
  也能改 `.lua` / `.vue` 这类 dyn UTI。
- **`com.apple.LaunchServices/com.apple.launchservices.secure` 不能用 CFPreferences 写。**
  `CFPreferencesAppSynchronize` 返回 true、`CFPreferencesCopyAppValue` 读回也有值，
  但磁盘上的 plist 永远不出现该条目 —— 静默失败。只有 LaunchServices API 写得进去。
