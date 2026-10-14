# Apple-Buyer 图标

用户指定：恶魔双手用力抓住、挤压一个成年的 Apple 标志拟人角色，挤出许多小 Apple 标志。采用非写实卡通，红、白、炭黑配色，透明背景，无文字。

源文件：`assets/app-icon.png`（1254 × 1254，RGBA），使用内置 `image_gen` 生成，已保存在项目中。

侧栏直接引用源文件；平台图标在 `src-tauri/icons`。重新生成平台格式：

```sh
pnpm exec tauri icon assets/app-icon.png -o src-tauri/icons
```

仅保留桌面使用的 PNG、ICNS 和 ICO；无需提交 Android、iOS 和 Windows Store 的额外产物。

## 生成提示词

Create one polished macOS / Windows application icon, square composition with transparent outer background, no text or letters, suitable for an app named Apple-Buyer. Main subject: a mischievous red cartoon devil with small curved horns and two big hands, visibly gripping and squeezing a full-grown adult anthropomorphic Apple logo character very tightly. The character's body is unmistakably the white Apple corporate logo silhouette with the bite on the right and its detached leaf above, given small adult-proportioned arms and legs and a comically strained facial expression. The devil is larger, positioned behind the Apple character, its BOTH hands pressing strongly into the Apple character from each side, making the white body visibly squashed. A plentiful stream of miniature white Apple logo shapes (with the characteristic bite and leaf) spills out below as if being harvested, around 8–12 clean recognizable mini logos in a compact cascade. Humorous non-graphic cartoon slapstick: no blood, no wounds. Strong readable silhouette, excellent app-icon craft, thick clean contours, flat red/white/charcoal color blocks with restrained dimensional shading, bold contrast. Keep a compact centered composition with safe margins, readable at small dock sizes. Transparent pixels outside the mascot, no background scene, no mockup, no text, no badge, no watermarks. Render at high resolution, one final icon.
