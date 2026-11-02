# SonicCaption 浏览器扩展初步逆向分析

对 Chrome 扩展「实时双语视频音频翻译 – SonicCaption」的静态代码阅读 + 运行时抓包结果。

| 项 | 值 |
| --- | --- |
| 扩展名称 | 实时双语视频音频翻译 – SonicCaption |
| 扩展 ID | `mlmgeakkomnhoblldciflmoeefmdaaha` |
| 版本 | 1.3.1（Manifest V3） |
| 本地路径 | `~/Library/Application Support/Google/Chrome/Default/Extensions/mlmgeakkomnhoblldciflmoeefmdaaha/1.3.1_0/` |
| 分析方式 | 反混淆阅读打包产物 + 注入 WebSocket 钩子实测（日语视频 → 中文，127 s 音频） |
| 分析日期 | 2026-10-26 |

Last Validated: Not yet validated

---

## 1. 结论摘要

1. **没有任何本地 ASR / 翻译模型。** 全量搜索 `onnx` / `wasm` / `whisper` / `transformers` / `SpeechRecognition` 零命中。音频以裸 PCM 二进制分片发往 `wss://ws.soniccaption.com/realtime`，识别与翻译全部在服务端完成。
2. **扩展是"音频管道 + 字幕外壳"。** 真正的工作只有三件：抓标签页音频、把服务端回来的 token 拼成通顺句子、把字幕画到页面上。
3. **服务端 ASR 是 60 ms 帧同步的流式 CTC/transducer**，逐 token 带语种标签和置信度，大概率自建推理服务（sherpa-onnx / k2 / WeNet / FunASR 这一族），而非调用商业 API。
4. **原文延迟实测中位 289 ms，译文延迟实测中位 1.74 s。** 官网 "Subtitles appear in ~0.5s" 只对原文成立；译文因为要等整句，实际约 2 s。
5. **隐私政策与实现不符。** privacy 页称音频 "processed locally"，但实测 127 s 音频全量上行。

---

## 2. 组件架构

MV3 标准分工，五个部件：

| 组件 | 文件（体积） | 职责 |
| --- | --- | --- |
| Service Worker | `background.js`（750 KB） | 编排：`chrome.tabCapture.getMediaStreamId` → 建 offscreen 文档 → 注入 content script → 转发字幕 → 历史落库/云同步 → 认证 → 埋点 |
| Offscreen 文档 | `offscreen.html` + `offscreen.js`（28 KB） | 真正干活：`getUserMedia` + `AudioWorklet` + WebSocket 客户端 |
| Content Script | `overlayContent.js`（276 KB） | React 18 字幕浮层 |
| Popup | `popup.js`（336 KB）+ `10.js`/`277.js`/`354.js` | React + Emotion + lucide 图标的设置面板 |
| History 页 | `history.html` + `history.js`（291 KB） | 独立扩展页，展示本地 IndexedDB + 云端历史 |

打包产物为 webpack（chunk 名 `reactboilerplate`）。`10.js` 是 React，`277.js` 是 CSS-in-JS 运行时，`661.js` 是 lucide 图标，`354.js` 是文案/定价字符串。

用 offscreen 文档的原因是 MV3 的 service worker 无 DOM、不能长期跑 `AudioWorklet`。`chrome.offscreen.createDocument` 的 justification 参数保留了中文原文：

```js
chrome.offscreen.createDocument({
  url, reasons: ["AUDIO_PLAYBACK", "BLOBS"],
  justification: "实时字幕：通过 offscreen 维持与后端的 WebSocket 连接"
})
```

补充：`background.js` 中 **没有 `closeDocument` 调用**（0 处命中），offscreen 文档一旦创建就常驻到 Chrome 退出。这一点对调试有影响（见 §9）。

---

## 3. 端到端音频链路

```
标签页音频
  └─ chrome.tabCapture.getMediaStreamId({targetTabId})     background 申请，streamId 递给 offscreen
      └─ getUserMedia({ audio: { mandatory: { chromeMediaSource: "tab",
                                              chromeMediaSourceId: streamId } },
                        video: false })
          └─ AudioContext → AudioWorkletNode("pcm-capture")
              └─ 每 20 ms 输出一帧（chunkSize = round(0.02 * sampleRate)）
                  多声道取均值降混单声道 → Float32 转 Int16
                  → ws.send(ArrayBuffer)          裸二进制，无容器、无压缩
```

要点：

- `video: false`，**画面帧从未离开标签页**，只有音频。
- 不做重采样，直接用硬件采样率（通常 48 kHz），适配责任在服务端。
- worklet 用 `port.postMessage(chunk, [chunk.buffer])` 零拷贝转移。
- 一次只抓一个标签页：用 `getCapturedTabs` + 一个 Set 记录已注入的 tab。

---

## 4. 通信协议（实测）

### 4.1 连接

几乎所有上下文塞进 query string，服务端因此可无状态：

```
wss://ws.soniccaption.com/realtime
  ?translation=one_way          # one_way | none | transcription
  &source_language=auto         # 60 种语言 code
  &target_language=zh
  &display_mode=bilingual       # bilingual | translation_only，切换时走消息不重连
  &idToken=<Firebase ID token>
  &taskId=20261026190413fMsZ    # yyyymmddHHMMSS + 6 位随机字母数字
  &tabTitle=<页面标题>
  &tabUrl=<URL>
```

上行只有两种帧：

- 二进制：PCM 分片
- 文本：`{"type":"display_mode","mode":"bilingual"}`（用户中途切双语/单语时实时告知）

下行消息（实测顺序）：

```
+0.43s  {"type":"auth_ok","uid":"<firebase uid>"}        ← 客户端代码未处理此消息
+0.58s  {"type":"ready"}                                  ← 此后才开始推音频
+1.85s  {"tokens":[…],"final_audio_proc_ms":0,"total_audio_proc_ms":960}   ← 首字
稳态    每 135 ms 一条（p90 321 ms，约 8 条/秒）
```

其他下行类型（代码中存在，本次未触发）：

- `{"type":"error","reason":"insufficient_balance","balance":N}` → 客户端主动 `close()`
- `{"type":"transcript"|"partial"|"final","text":…}` → 旧版兼容分支

### 4.2 token 结构

```json
{
  "text": "や",
  "start_ms": 360, "end_ms": 420,
  "confidence": 0.922,
  "is_final": false,
  "language": "ja",
  "translation_status": "original"
}
```

- **每条 `tokens` 是整句快照，不是增量 diff**：同一句会随识别推进反复重发，内容会漂移、会被修订。客户端那坨最难读的合并函数就是为此而写。
- 翻译 token 额外带 `source_language`，且**完全没有 `start_ms` / `end_ms`**。所以客户端只能拿原文时间轴去锚定译文。
- 客户端代码里的 `<end>` 哨兵字符串实测 **0 次出现**，是死代码；`is_final: true` 真实存在（本次 534 条）。
- `translation_status` 取值：`original` / `translation` / `none`（`none` 是"语种未定"的模糊态，客户端有启发式归类）。
- `start_ms` / `end_ms` 是**音频流内相对毫秒**。客户端用 `Date.now() - start_ms` 反推 `audioEpochAt`，把字幕钉到墙上时钟，历史记录时间戳因此准确。

---

## 5. 服务端技术路线推断

### 5.1 ASR 指纹

从实测 token 统计得出的硬证据：

| 观测 | 数值 |
| --- | --- |
| `start_ms % 60` | 全部为 `0` |
| token 时长集合 | `{0, 60, 240}` ms |
| 每 token `confidence` | 0.211 – 1.0 |
| 每 token `language` | 逐 token 语种（本次 `ja` 2641 / `ro` 14 / `zh` 327 条累计） |
| `total_audio_proc_ms` 增量 | `{120, 240, 360, …}`（约 120 ms 一块） |

**结论：严格 60 ms 帧同步网格，一帧一个 token，帧级置信度 + 帧级语种识别。**

这排除了两类常见方案：

- Whisper 类 seq2seq：30 s 窗、无定长帧网格、不输出帧级置信度。
- 典型商业流式 API（Deepgram / Azure Speech）：给的是**词级可变时长**时间戳，不是单帧单 token。

最接近的是帧同步流式 CTC / RNN-T 家族（sherpa-onnx、k2、WeNet、FunASR 一类），多语种 + 逐帧 LID。加上自研 WebSocket 网关做鉴权、句子切分、余额计费，整体形态更像**自建推理服务**而非转售 API。

### 5.2 MT 指纹

- 译文按**整句**下发，无时间轴，只在句子成型后出现。
- 首次译文出现在原文同句首 token 之后**中位 1.74 s**（0.10 – 4.56 s）→ 等句末才翻译。
- 语种翻车时会硬翻：见 §6。

### 5.3 语种与目标过滤

客户端有个 `p()` 函数会丢弃 `language` 与目标语言不匹配的翻译 token——服务端会把偶发的错误语种译文也一起推下来，客户端在兜底。

---

## 6. 实测失败模式

音乐 / 笑声段语种识别翻车，**原文里直接混入罗马尼亚语字符**：

```
原文  やっと来ま aすțiか g、lumですね。      （language 标签从 ja 翻成 ro）
译文  终于要来了，是吧。                    （MT 拿到脏原文仍翻回通顺中文）
```

这说明 ASR 是逐帧独立判语种的，噪声段会整段切到别的语言；而 MT 对带脏字符的输入有一定纠错能力。也解释了 `p()` 过滤函数的必要性。

---

## 7. 延迟实测

| 环节 | 实测 |
| --- | --- |
| WS 建连 → `auth_ok` | 430 ms |
| → `ready` | 580 ms |
| 冷启动首字（含模型预热） | 1.85 s |
| **原文稳态延迟**（墙上时间 − 已处理音频时长） | **中位 289 ms**，范围 216–455 ms |
| **译文落后原文同句** | **中位 1.74 s**，范围 0.10–4.56 s |
| 下行消息节奏 | 中位 135 ms / 条（p90 321 ms） |

所以"原文 ~0.3 s"是真实达成的；用户看到的中文译文实际约 2 s 延迟。

---

## 8. 客户端工程细节

这部分是扩展里真正有技术含量的东西。

### 8.1 背压

```js
if (ws.bufferedAmount > 262144) {          // 256 KB
  if (!stalledSince) stalledSince = now;
  if (now - stalledSince > 8000) reconnect("audio_backpressure");
}
```

持续 8 s 阻塞即判定链路堵死，主动断开重连。

### 8.2 重连

- 指数退避，base 1 s、上限 30 s、叠加 0–200 ms 抖动
- `ready` 前最多 5 次，`ready` 后最多 2 次
- 每次重连都回 background 要**新的 idToken**（`offscreenRequestAuth`），因为 Firebase token 会过期

### 8.3 音频采集恢复

track `ended` 或采集失败 → `forceRestart` 重新申请 streamId，12 次重试、500 ms→10 s 退避。

### 8.4 文本合并（最难读的一段）

`Pe()` 函数解决流式快照的缝合问题，依次尝试：

1. 整段替换（新文本是旧文本的前缀 / 旧文本是新文本的子串）
2. 公共前缀长度 ≥ 60% → 整体替换
3. 尾部 256 字符窗口内的锚点拼接
4. 最长重叠后缀匹配拼接
5. 兜底直接串接

字幕抖动、重复词全靠这里挡。

### 8.5 浮层

- React 18 渲染，**普通 DOM + inline style，没有 Shadow DOM**（所以可能被站点样式污染）
- `position: fixed` + `z-index: 2147483646`
- 可拖拽，`pointer-events` 动态开关（平时 `none`，避免挡住视频控件）
- 监听 `fullscreenchange` 重新挂载，规避全屏时浮层不可见

### 8.6 服务端消息的节流

字幕推给浮层前按时间戳节流，避免每个 token 都触发 React 重渲染。

---

## 9. 认证、计费、存储、埋点

| 能力 | 实现 |
| --- | --- |
| 登录 | `chrome.identity.launchWebAuthFlow` + `getRedirectURL` → Google OAuth → Firebase（`securetoken.google.com`）换 idToken |
| 计费 | Stripe Checkout（`soniccaption.com/checkout/stripe/soniccaption`），订阅管理跳 Stripe Portal；另有 `checkout-token` 接口 |
| 余额 | 服务端 `insufficient_balance` 直接断流；客户端上报 `quota_exhausted` 事件 |
| 历史（本地） | IndexedDB（封装里抛 `DatabaseClosedError`），记录字段 `LocalId / StartTime / EndTime / SourceText / TargetText` |
| 历史（云端） | `POST ws.soniccaption.com/api/history`（带 idToken），失败有 `FinalizeRetry` 补偿 + `chrome.alarms` 定时重试 |
| 埋点 | PostHog（`task_started` / `quota_exhausted`，属性含 from_lang / to_lang / domain / tabTitle） |
| 错误上报 | Sentry |
| 遗留 | 仍保留 Firebase 项目 `easyhighlight` 的 `insertUser` 云函数接口，说明产品是从另一个项目演化来的 |

支持的语种表 60 项，含 RTL 集合 `ar / he / fa / ur`（代码里单独建了 `Set`）。

---

## 10. 与隐私政策的差异

privacy 页原文：

> Audio from the active tab is processed locally to generate text.

**实测与代码均不支持这一说法：**

- 代码里没有任何本地识别模型（§1）
- 抓包记录 6444 帧 × 20 ms ≈ **127 s 音频全量上行**到 `ws.soniccaption.com`
- 本地只做 PCM 装箱和文本拼接

同页对第三方的描述是泛指的 "third-party providers (such as speech-to-text and translation APIs)"，未点名任何 ASR / MT 供应商。

---

## 11. 复现方法

不修改扩展任何文件，只在 offscreen 文档内存里 hook。

### 11.1 采集端（Python，标准库）

```python
import json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = "/tmp/sc/ws.log"

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        with open(LOG, "a") as f:
            f.write(json.dumps({"recv": time.time(), "body": raw}, ensure_ascii=False) + "\n")
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
```

### 11.2 注入钩子

在 Chrome 里打开 `chrome://extensions` → 找到 SonicCaption → **检查视图：offscreen.html** → Console 中执行：

```js
(()=>{const W=WebSocket,L=o=>fetch("http://127.0.0.1:8765/",{method:"POST",mode:"no-cors",body:JSON.stringify(o)});
window.WebSocket=class extends W{constructor(u,p){super(u,p);L({e:"open",u:String(u).replace(/idToken=[^&]*/,"idToken=REDACTED")});
this.__n=0;const s=this.send.bind(this);this.send=b=>{this.__n++;return s(b)};
this.addEventListener("message",x=>L({e:"msg",n:this.__n,d:x.data}));
this.addEventListener("close",x=>L({e:"close",c:x.code,r:x.reason,n:this.__n}))}}})();
```

`n` 是已发送的 PCM 帧数（每帧 20 ms），`n × 20 ms` 即"推流至该 token 时的音频流时间"。

### 11.3 触发新连接

钩子只影响**新创建**的 socket，因此装完后要按 `Alt+Shift+S` 停一次、再按一次重开转录（或在页面里触发重连）。

### 11.4 注意事项

- 抓到的 query string 含 Firebase idToken，必须脱敏（钩子已做 `idToken=REDACTED`）；返回体里的 `uid` 也属于账号信息，落盘日志不要进版本库。
- offscreen 文档常驻（无 `closeDocument`），所以停/开转录不会丢钩子；Chrome 重启或扩展重载后钩子消失。
- Chrome DevTools Console 首次粘贴会拦一次，手敲 `allow pasting` 回车即可。

---

## 12. 未验证与局限

- **上行采样率与带宽未测**：本次没有 hook `send()` 的字节数，只知道分片节奏是 20 ms 一帧。
- **服务端具体模型无法确证**：60 ms 帧同步网格是强指纹，但"就是 sherpa-onnx / FunASR"属于推断，需服务端信息才能定论。
- **计费单价未验证**：余额消耗速率取决于账号套餐，本次未采集。
- **多语种组合未覆盖**：只测了日语 → 中文、`translation=one_way`、`display_mode=bilingual`；`transcription` 模式、双向模式、60 种语言中的其他组合行为未验证。
- **本次日志未入库**：原始 866 条记录（含 uid）只留在本机临时目录，未纳入版本库。

---

## 附：一句话总结

扩展 = tabCapture 取流 + AudioWorklet 切 20 ms PCM + 裸二进制 WebSocket 上行 + 整句快照合并 + React 浮层；后端 = 自研 60 ms 帧同步流式多语种 ASR（逐帧 token + 语种 + 置信度）→ 等句末触发 MT → 每约 135 ms 推一版整句快照，鉴权与余额计费都在网关层。
