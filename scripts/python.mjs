import { spawnSync } from "node:child_process";
const candidates =
  process.platform === "win32"
    ? [["python"], ["py", "-3"], ["python3"]]
    : [["python3"], ["python"]];
for (const [command, ...prefix] of candidates) {
  const check = spawnSync(
    command,
    [
      ...prefix,
      "-c",
      "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)",
    ],
    { stdio: "ignore", windowsHide: true },
  );
  if (check.status !== 0) continue;
  const result = spawnSync(command, [...prefix, ...process.argv.slice(2)], {
    stdio: "inherit",
    windowsHide: true,
  });
  process.exit(result.status ?? 1);
}
console.error(
  "开发任务需要 Python 3.10+。打包后的 Apple-Buyer 应用无需安装 Python。",
);
process.exit(1);
