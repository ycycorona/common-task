# 中文字体方案

free-arm（Ubuntu 24.04）在 2026-11-06 之前**一个中文字都没有**，浏览器截图全是方块（`fc-list :lang=zh` 返回 0 条）。本目录是补上之后定下来的方案，换机器或重装用 `bash fonts/install.sh` 一条命令复现。

## 三档通用族的映射

| 通用族 | 落到 | 用在哪 |
| :--- | :--- | :--- |
| `sans-serif` | **Noto Sans CJK SC**（思源黑体） | 网页界面、GTK/Qt 程序默认字 |
| `serif` | **霞鹜文楷 LXGW WenKai** | 正文阅读 |
| `monospace` | **霞鹜文楷 Bright Code NF** | 终端、编辑器、代码（含 Nerd Font 图标） |

规则写在 [`99-local-zh-prefer.conf`](99-local-zh-prefer.conf)，装到 `/etc/fonts/conf.d/`。三条 `match` 带 `lang=zh` 决定中文落到哪个族；两条 `<alias binding="same">` 管**不声明语言**的字符级回退（这样西文仍留 DejaVu，CJK 回退优先简体 SC 而不是 JP）。删掉该文件 + `fc-cache -f` 即回滚。

## 字体从哪来

| 字体 | 来源 | 占用 |
| :--- | :--- | :--- |
| 霞鹜文楷（Light/Regular/Bold + 等宽三档） | `apt install fonts-lxgw-wenkai`（1.315） | 111 MB |
| Noto Sans/Mono/Serif CJK（SC/TC/JP/KR/HK） | `apt install fonts-noto-cjk`（20230817） | 89 MB |
| LXGW Bright Code NF（ExtraLight/Light/Regular + 斜体） | `lxgw/LxgwBright-Code` release `26.10.22` 的 `LxgwBrightCodeNF.7z`，apt 没有 | 97 MB |

总计 297 MB。上游只发 ExtraLight / Light / Regular 三档，**没有 Bold**——终端里的粗体由渲染器合成或回退。

## 为什么不拿霞鹜文楷包办全部

霞鹜文楷是楷体，做界面偏「文艺」且字距宽；它的等宽版 `LXGW WenKai Mono` 又缺 Nerd Font 图标（`U+F015`、`U+F07B` 等只有 NF 版有，`U+E0B0` 两版都有）。所以界面用思源黑体、终端用 Bright Code NF，文楷留给正文与缺字兜底。

## 已知边界

- **snap 版 Chromium 不读 `/etc/fonts/conf.d` 里的自定义规则**（`snap run --shell chromium -c 'fc-match monospace'` 返回 DejaVu Sans Mono，宿主上却是 NF）。网页里写 `font-family: monospace` 时图标是框；写死字体名的页面正常。终端与编辑器不受影响，它们显式指定字体。
- Bright Code **不含 Bold 字重**。
- VNC 桌面里的实际渲染未单独验证。
