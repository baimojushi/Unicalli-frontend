# UniCalli Mobile UI Architecture

手机端现在作为独立 UI 层维护。

- `unicalli_ui.css`：桌面视觉与 >= 768px 的紧凑桌面/平板规则。
- `unicalli_ui.js`：跨端生成、长卷 segment、重写、计时、主题等核心交互。
- `unicalli_mobile.css`：<= 767px 的唯一手机布局规则来源。
- `unicalli_mobile.js`：手机题写面板、设置面板、VisualViewport、软键盘、安全区域和布局测量。

## 移动端布局原则

1. 顶栏、动作栏、阅卷区、题写区各自占据明确空间。
2. 阅卷区通过 `--mobile-reserved-bottom` 动态避让底部题写区与设置区。
3. 长卷改成纵向阅读 feed；128x640 等书法图通过 `object-fit: contain` 保持纵横比。
4. 每段“重写”按钮常驻触控，不依赖 hover。
5. 题写面板生成时自动收起，阅卷空间随之扩大。
6. 设置区打开时会计入保留高度，不压住阅卷内容。
7. `visualViewport` 用于软键盘出现后的底部工作区定位。
8. 手机输入字号固定为 16px，兼顾 iOS 防自动缩放与 Android 可读性。
9. 支持 safe-area、竖屏/横屏切换和 reduced motion。

## Breakpoint

手机端统一为 `max-width: 767px`。
旧的 640 / 480 / 390 多层手机布局已从桌面 CSS/JS 中移除。
390px 仅保留极窄屏尺寸微调，不再形成第二套布局。
