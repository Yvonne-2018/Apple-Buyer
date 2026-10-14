// 本地界面录屏；所有账号、库存、订单和付款码均为模拟数据。
import { chromium, expect } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const output = join(root, "docs/videos/template-checkout-demo.mp4");
const images = join(root, "docs/images");
const rawDir = join(root, "artifacts/demo-recording");
await Promise.all([
  mkdir(join(root, "docs/videos"), { recursive: true }),
  mkdir(images, { recursive: true }),
  mkdir(rawDir, { recursive: true }),
]);
const socket = createServer();
await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
const port = socket.address().port;
await new Promise((resolve) => socket.close(resolve));
const base = `http://127.0.0.1:${port}`;
const server = spawn(
  process.execPath,
  [
    join(root, "node_modules/vite/bin/vite.js"),
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
    "--strictPort",
    "--mode",
    "local-web",
  ],
  { cwd: root, stdio: "ignore" },
);
let browser;
let context;
const started = Date.now();
try {
  for (;;) {
    try {
      if ((await fetch(base)).ok) break;
    } catch {
      /* Vite 尚在启动。 */
    }
    if (Date.now() - started > 30000) throw new Error("演示页面启动超时");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const python = join(
    root,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  const qr =
    "data:image/png;base64," +
    execFileSync(
      python,
      [
        "-c",
        "import qrcode,io,base64; from qrcode.image.pure import PyPNGImage; b=io.BytesIO(); qrcode.make('DEMO ONLY - NOT A PAYMENT CODE',image_factory=PyPNGImage).save(b); print(base64.b64encode(b.getvalue()).decode())",
      ],
      { encoding: "utf8" },
    ).trim();
  browser = await chromium.launch();
  context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    recordVideo: { dir: rawDir, size: { width: 1440, height: 1000 } },
    locale: "zh-CN",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) =>
    route
      .request()
      .url()
      .startsWith(base + "/")
      ? route.continue()
      : route.abort(),
  );
  await page.addInitScript(
    ({ qr }) => {
      const accounts = [];
      const tasks = [];
      const templates = [];
      let callback = () => {};
      let counter = 0;
      let defaultId = "";
      const store = () =>
        structuredClone({
          schema_version: 1,
          default_id: defaultId,
          templates,
        });
      const update = (task) => {
        task.updated_at = Date.now() / 1000;
        callback(structuredClone(task));
      };
      const contact = {
        lastName: "演示",
        firstName: "用户",
        emailAddress: "demo@example.invalid",
        fullDaytimePhone: "13800000000",
        nationalIdLast4: "123X",
      };
      window.__DUO_KILLER_TEST__ = {
        snapshot: async () =>
          structuredClone({ accounts, tasks, data_dir: "/demo-only" }),
        subscribe: async (handler) => {
          callback = handler;
          return () => {};
        },
        add: async (email) => {
          const account = {
            id: "demo-account",
            email,
            name: "演示账号",
            pending: true,
            preset_sku: "MLH74FE",
            preset_quantity: 1,
          };
          accounts.push(account);
          return structuredClone(account);
        },
        listTemplates: async () => store(),
        templateSource: async () => ({
          contact,
          delivery: { state: "四川", city: "成都", district: "锦江区" },
          address: { street: "演示地址，不用于真实订单" },
        }),
        saveTemplate: async (_, value, isDefault) => {
          const copy = structuredClone(value);
          copy.id ||= crypto.randomUUID();
          copy.revision++;
          const index = templates.findIndex((t) => t.id === copy.id);
          if (index < 0) templates.push(copy);
          else templates[index] = copy;
          if (isDefault) defaultId = copy.id;
          return store();
        },
        start: async (accountId, action, params) => {
          const task = {
            id: `demo-task-${++counter}`,
            account_id: accountId,
            action,
            status: "running",
            stage: "本地演示",
            created_at: Date.now() / 1000,
            updated_at: Date.now() / 1000,
            prompt: null,
            result: null,
            order_submitted: false,
            logs: [],
          };
          tasks.unshift(task);
          if (action === "enroll" || action === "login") {
            task.status = "waiting";
            task.prompt = {
              id: "demo-password",
              kind: "password",
              label: "输入 Apple 账号密码",
            };
          } else if (action === "purchase") {
            const template = templates.find((t) => t.id === params.template_id);
            if (
              !template ||
              template.pickup.store_ids.join(",") !== "R502,R580"
            )
              throw new Error("演示门店顺序不符合录制场景");
            const stages = [
              "模拟：模板校验通过，核对目标 SKU 与数量",
              "模拟：Apple 成都万象城当前不可取货，切换下一家所选门店",
              "模拟：已选择 Apple 成都太古里，并确认该门店取货时段",
              "模拟：使用模板联系人、个人发票和支付宝",
              "模拟：商品、数量及总额未超过模板上限，创建演示订单",
            ];
            stages.forEach((stage, i) =>
              setTimeout(
                () => {
                  task.stage = stage;
                  task.logs.push(stage);
                  update(task);
                },
                1600 * (i + 1),
              ),
            );
            setTimeout(() => {
              task.status = "succeeded";
              task.stage = "演示订单已创建，等待付款（不可付款）";
              task.result = {
                success: true,
                status: "pending_payment",
                order_number: "DEMO-NOT-PAYABLE",
                amount_due: "RMB 138（模拟）",
                template_name: template.name,
                qr_data: qr,
              };
              update(task);
            }, 10000);
          } else if (action === "orders") {
            task.status = "succeeded";
            task.result = {
              success: true,
              orders: [
                {
                  order_number: "DEMO-NOT-PAYABLE",
                  items: [
                    {
                      name: "抛光布（模拟订单）",
                      quantity: 1,
                      delivery_info: "待付款（演示，不发生支付）",
                    },
                  ],
                },
              ],
              message: "模拟订单读取完成",
            };
          } else {
            task.status = "succeeded";
            task.result = {
              success: true,
              logged_in: true,
              name: "演示账号",
              apple_id: "demo@example.invalid",
              scope: "account",
            };
          }
          update(task);
          return task.id;
        },
        reply: async (_, taskId, requestId, value) => {
          const task = tasks.find((t) => t.id === taskId);
          if (task.prompt.kind === "password") {
            task.prompt = {
              id: "demo-code",
              kind: "code",
              label: "设备六位验证码：",
            };
          } else {
            accounts[0].pending = false;
            task.status = "succeeded";
            task.prompt = null;
            task.result = {
              success: true,
              logged_in: true,
              scope: "account",
              apple_id: accounts[0].email,
              name: "演示账号",
            };
          }
          update(task);
        },
      };
    },
    { qr },
  );
  await page.goto(base);
  await expect(page).toHaveTitle("Apple-Buyer");
  await expect(page.locator(".brand")).toContainText("Apple-Buyer");
  await page.addStyleTag({
    content: `html{scroll-padding-top:110px;scroll-padding-bottom:155px}body{padding-bottom:115px!important}.demo-caption{position:fixed;z-index:99999;left:24px;right:24px;bottom:20px;border-radius:14px;padding:18px 24px;background:#17212ef5;color:#fff;box-shadow:0 5px 24px #0002;pointer-events:none;font:17px/1.6 -apple-system,'PingFang SC',sans-serif}.demo-caption strong{display:block;font-size:21px}.demo-caption small{color:#c6cfdb}.demo-mark{position:fixed;z-index:99998;right:24px;top:12px;padding:7px 12px;border-radius:8px;background:#17212e;color:#fff;font:13px -apple-system,'PingFang SC',sans-serif;pointer-events:none}`,
  });
  await page.evaluate(() => {
    const el = document.createElement("div");
    el.className = "demo-caption";
    el.id = "demo-caption";
    document.body.append(el);
    const mark = document.createElement("div");
    mark.className = "demo-mark";
    mark.textContent = "本地录制 · 全程模拟数据 · 不可付款";
    document.body.append(mark);
  });
  const chapters = [];
  const videoStart = Date.now();
  const pause = (ms = 1600) => page.waitForTimeout(ms);
  async function caption(title, detail) {
    chapters.push({ seconds: (Date.now() - videoStart) / 1000, title, detail });
    await page.evaluate(
      ({ title, detail }) => {
        const box = document.getElementById("demo-caption");
        box.replaceChildren();
        const heading = document.createElement("strong");
        heading.textContent = title;
        const desc = document.createElement("small");
        desc.textContent = detail;
        box.append(heading, desc);
      },
      { title, detail },
    );
    console.log(title);
    await pause(1200);
  }
  async function focus(locator) {
    await locator.evaluate((el) => el.scrollIntoView({ block: "center" }));
    await pause(350);
  }
  async function click(locator) {
    await focus(locator);
    const box = await locator.boundingBox();
    if (box)
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, {
        steps: 12,
      });
    await locator.click();
    await pause(500);
  }
  async function fill(label, value) {
    const input = page.getByLabel(label, { exact: true });
    await focus(input);
    await input.fill(value);
    await pause(500);
  }
  async function select(label, option) {
    await click(page.getByRole("combobox", { name: label, exact: true }));
    await page
      .getByRole("listbox")
      .getByRole("option", { name: option, exact: true })
      .click();
    await pause(600);
  }
  async function shot(name) {
    await page.screenshot({ path: join(images, name) });
  }

  await caption(
    "01 · Apple-Buyer 登录准备",
    "首次添加账号并完成验证；此处使用虚构账号、密码和验证码。",
  );
  await pause(1400);
  await fill("Apple 账号", "demo@example.invalid");
  await click(page.getByRole("button", { name: "继续登录", exact: true }));
  await fill("密码", "demo-password-not-real");
  await page.getByRole("checkbox", { name: /^保存在本机文件/ }).uncheck();
  await click(page.getByRole("button", { name: "继续", exact: true }));
  await fill("六位验证码", "012345");
  await shot("template-demo-01-login.png");
  await click(page.getByRole("button", { name: "继续", exact: true }));
  await expect(
    page.getByRole("button", { name: "新建模板", exact: true }),
  ).toBeVisible();

  await caption(
    "02 · 创建下单模板",
    "从现有配置导入联系人；设置商品、数量和含运费的总额上限。",
  );
  await click(
    page.getByRole("button", { name: "从现有配置创建", exact: true }),
  );
  await fill("模板名称", "抛光布 · 成都门店自提");
  await fill("订单总额上限（元，含运费）", "200");
  await select("模板收货方式", "门店自提");
  await pause(1000);

  await caption(
    "03 · 城市列表与门店多选",
    "目录包含中国大陆 25 个城市、49 家直营店；只显示所选城市的门店。",
  );
  await select("模板提货城市", "成都");
  await click(
    page.getByRole("checkbox", { name: "选择 Apple 成都太古里", exact: true }),
  );
  await click(
    page.getByRole("checkbox", { name: "选择 Apple 成都万象城", exact: true }),
  );
  await caption(
    "04 · 调整门店尝试顺序",
    "上移后优先尝试成都万象城，不可选时再尝试成都太古里。",
  );
  await click(
    page.getByRole("button", { name: "上移 Apple 成都万象城", exact: true }),
  );
  await focus(page.locator(".pickup-store-select"));
  await shot("template-demo-02-stores.png");
  await pause(2400);

  await caption(
    "05 · 提前配置取货时间与执行选项",
    "可限定日期、时间范围；本演示仅买原 SKU，不配置替代商品。",
  );
  await select("模板取货时间", "限定日期与时间范围");
  const pickupDay = (offset) =>
    new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(
      new Date(Date.now() + offset * 86400000),
    );
  await fill("取货开始日期", pickupDay(1));
  await fill("取货结束日期", pickupDay(2));
  await fill("每天最早取货时间", "10:00");
  await fill("每天最晚取货时间", "18:00");
  await focus(page.getByLabel("模板姓氏", { exact: true }));
  await pause(2000);
  await click(
    page.getByRole("checkbox", { name: "设为此账号默认模板", exact: true }),
  );
  await pause(1500);
  await click(page.getByRole("button", { name: "保存下单模板", exact: true }));
  await focus(page.locator(".template-summary"));
  await expect(
    page.getByRole("button", { name: "按模板一键下单", exact: true }),
  ).toBeEnabled();
  await caption(
    "06 · 保存模板，下次直接使用",
    "确认商品、预算、城市与门店顺序；模板按账号保存，可继续编辑或复制。",
  );
  await shot("template-demo-03-summary.png");
  await pause(2500);

  await caption(
    "07 · 一键执行与自动切店（模拟）",
    "模拟首选门店不可取货，切换下一家；执行过程中无需重复选择业务信息。",
  );
  await click(
    page.getByRole("button", { name: "按模板一键下单", exact: true }),
  );
  await focus(page.getByRole("region", { name: "当前任务", exact: true }));
  await expect(
    page.getByRole("heading", { name: "订单已创建，等待付款", exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await focus(page.locator(".payment"));
  await caption(
    "08 · 到达付款二维码",
    "此二维码只编码演示文本，不可付款；真实使用时由本人扫码并查询订单确认。",
  );
  await shot("template-demo-04-payment.png");
  await pause(4000);
  await caption(
    "09 · 查询订单，确认实际状态",
    "进入订单页主动查询；当前展示模拟待付款订单，不表示已支付。",
  );
  await click(page.getByRole("button", { name: "订单", exact: true }));
  await click(page.getByRole("button", { name: "查询订单", exact: true }));
  await expect(
    page.getByRole("cell", { name: "待付款（演示，不发生支付）", exact: true }),
  ).toBeVisible();
  await shot("template-demo-05-orders.png");
  await pause(3500);
  await caption(
    "Apple-Buyer 演示完成 · 配置一次，按序选店，执行到付款码",
    "Mac M 系列 DMG / Windows 64 位 EXE 已支持 GitHub Actions 打包。",
  );
  await pause(3500);
  if (errors.length) throw new Error(errors.join("\n"));
  const video = page.video();
  await context.close();
  context = null;
  const videoPath = await video.path();
  execFileSync(
    "ffmpeg",
    [
      "-y",
      "-i",
      videoPath,
      "-an",
      "-vf",
      "fps=25",
      "-c:v",
      "libx264",
      "-preset",
      "fast",
      "-crf",
      "22",
      "-pix_fmt",
      "yuv420p",
      "-movflags",
      "+faststart",
      output,
    ],
    { stdio: "ignore" },
  );
  await writeFile(
    join(rawDir, "chapters.json"),
    JSON.stringify(chapters, null, 2) + "\n",
  );
  console.log(`视频已生成：${output}`);
} finally {
  await context?.close();
  await browser?.close();
  server.kill("SIGTERM");
}
