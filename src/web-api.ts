import type { Bridge } from "./api";
import type { Task } from "./types";

export function localWebBridge(): Bridge {
  // The launch secret lives in the URL fragment, never a request URL or Referer.
  const access = new URLSearchParams(location.hash.slice(1)).get("access");
  if (access) {
    sessionStorage.setItem("apple-buyer-web-access", access);
    history.replaceState(null, "", location.pathname);
  }
  const token =
    access || sessionStorage.getItem("apple-buyer-web-access") || "";
  const headers = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
  async function request(path: string, init: RequestInit = {}) {
    let response: Response;
    try {
      response = await fetch(`/api/${path}`, {
        ...init,
        headers,
        cache: "no-store",
      });
    } catch (error) {
      if (init.signal?.aborted) throw error;
      throw new Error("本地服务未连接，请先启动 Apple-Buyer 本地 Web。");
    }
    if (!response.ok) {
      if (response.status === 404) {
        throw new Error(
          import.meta.env.DEV
            ? "本地服务未提供此接口（404），可能仍在运行旧版本。请在原启动终端按 Ctrl+C，再运行 pnpm dev:web，并从新链接打开页面。"
            : "本地服务未提供此接口（404）。请退出旧的本地 Web 服务，启动最新版本，并从新链接打开页面。",
        );
      }
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || `本地服务请求失败（${response.status}）`);
    }
    return response;
  }
  async function call<T>(name: string, args: object = {}): Promise<T> {
    return (
      await request(name, { method: "POST", body: JSON.stringify(args) })
    ).json();
  }
  return {
    snapshot: () => call("snapshot"),
    add: (email) => call("add_account", { email }),
    remove: (accountId) => call("remove_account", { accountId }),
    listTemplates: (accountId) => call("list_templates", { accountId }),
    templateSource: (accountId) => call("template_source", { accountId }),
    saveTemplate: (accountId, template, isDefault) =>
      call("save_template", { accountId, template, isDefault }),
    deleteTemplate: (accountId, templateId) =>
      call("delete_template", { accountId, templateId }),
    savePreset: (accountId, sku, quantity) =>
      call("save_preset", { accountId, sku, quantity }),
    start: (accountId, action, params) =>
      call("start_task", { accountId, action, params }),
    reply: (accountId, taskId, requestId, value) =>
      call("reply_task", { accountId, taskId, requestId, value }),
    cancel: (accountId, taskId) => call("cancel_task", { accountId, taskId }),
    subscribe: async (callback) => {
      const controller = new AbortController();
      const connect = () => request("events", { signal: controller.signal });
      // Fail initialization visibly if the launch link expired.
      const first = await connect();
      async function read(response: Response) {
        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        try {
          while (!controller.signal.aborted) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let separator: RegExpExecArray | null;
            while ((separator = /\r?\n\r?\n/.exec(buffer))) {
              const frame = buffer.slice(0, separator.index);
              buffer = buffer.slice(separator.index + separator[0].length);
              const data = frame
                .split(/\r?\n/)
                .filter((line) => line.startsWith("data:"))
                .map((line) => line.slice(5).trimStart())
                .join("\n");
              if (data && !controller.signal.aborted)
                callback(JSON.parse(data) as Task);
            }
          }
        } finally {
          await reader.cancel().catch(() => {});
        }
      }
      void (async () => {
        let response = first;
        while (!controller.signal.aborted) {
          try {
            await read(response);
          } catch {
            /* reconnect below */
          }
          while (!controller.signal.aborted) {
            await new Promise((resolve) => setTimeout(resolve, 1000));
            if (controller.signal.aborted) return;
            try {
              response = await connect();
              break;
            } catch {
              /* retry until disposed */
            }
          }
        }
      })();
      return () => controller.abort();
    },
  };
}
