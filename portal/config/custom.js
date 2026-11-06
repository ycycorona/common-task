// 按平台改写 Orca 入口的跳转。
// 依据 stablyai/orca 仓库：
//   src/main/startup/orca-protocol-registration.ts —— 桌面端注册 `orca://`（macOS 读 Info.plist，Linux 读 desktop entry，
//     Windows 由打包后的应用运行时注册）
//   src/shared/remote-runtime/pairing/pairing.ts   —— 手机端深链 `orca://pair?code=<base64url>`，注释写明移动 App 用
//     Expo Router + 自定义 scheme，所以同一个 `orca` scheme 手机也认；带不带 code 决定进配对页还是首页
//   docs: https://onorca.dev/docs/mobile —— iOS 走 App Store（orca-ide, id6766130217），Android 走 GitHub Releases APK
// 因此规则很简单：装了 Orca App 的系统（iOS/Android/macOS/Windows）跳 `orca://`；Linux 浏览器（如 free-arm，
// 没有 desktop entry、不注册 handler）留 web 地址。
// shortcut: 深链只到 App 首页，无法直接进某个 workspace/会话——那需要桌面 UI 生成的一次性 `orca://pair?code=`，不能静态化。
(() => {
  const WEB = "http://100.126.255.75:6768/";
  const ua = navigator.userAgent;
  const mobile = /iPhone|iPad|iPod|Android/i.test(ua);
  const hasApp = mobile || /Macintosh|Mac OS X|Windows/i.test(ua);
  const target = hasApp ? "orca://" : WEB;

  const apply = () => {
    document.querySelectorAll(`a[href="${WEB}"], a[data-orca="1"]`).forEach((a) => {
      a.dataset.orca = "1";
      if (a.getAttribute("href") !== target) a.setAttribute("href", target);
      a.title = hasApp
        ? "在 Orca 应用中打开（未安装则无反应，见 onorca.dev/docs/mobile）"
        : "在浏览器中打开 Orca Web";
    });
  };
  apply();
  new MutationObserver(apply).observe(document.body, { childList: true, subtree: true });
})();
