import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
if (!["darwin", "win32"].includes(process.platform)) {
  throw new Error("应用构建支持 macOS 和 Windows。");
}
const worker = join(root, "src-tauri/resources/worker");
if (!existsSync(worker)) throw new Error("请先运行 pnpm worker:build。");
const args = [
  join(root, "node_modules/@tauri-apps/cli/tauri.js"),
  "build",
  "--bundles",
  process.platform === "darwin" ? "app,dmg" : "nsis",
];
const result = spawnSync(process.execPath, args, {
  cwd: root,
  stdio: "inherit",
});
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);
console.log(
  process.platform === "darwin"
    ? "安装包：src-tauri/target/release/bundle/dmg/"
    : "安装程序：src-tauri/target/release/bundle/nsis/",
);
