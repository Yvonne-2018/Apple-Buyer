import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { Account, Action, Data, Snapshot, Task } from "./types";
import { localWebBridge } from "./web-api";
import type { OrderTemplate, TemplateStore } from "./templateModel";
export const isLocalWeb = import.meta.env.MODE === "local-web";
export interface Bridge {
  snapshot(): Promise<Snapshot>;
  add(email: string): Promise<Account>;
  remove(accountId: string): Promise<void>;
  listTemplates(accountId: string): Promise<TemplateStore>;
  templateSource(accountId: string): Promise<Data>;
  saveTemplate(
    accountId: string,
    template: OrderTemplate,
    isDefault: boolean,
  ): Promise<TemplateStore>;
  deleteTemplate(accountId: string, templateId: string): Promise<TemplateStore>;
  savePreset(
    accountId: string,
    sku: string,
    quantity: number,
  ): Promise<Account>;
  start(accountId: string, action: Action, params: Data): Promise<string>;
  reply(
    accountId: string,
    taskId: string,
    requestId: string,
    value: unknown,
  ): Promise<void>;
  cancel(accountId: string, taskId: string): Promise<void>;
  subscribe(callback: (task: Task) => void): Promise<() => void>;
}
declare global {
  interface Window {
    __DUO_KILLER_TEST__?: Bridge;
  }
}
const native: Bridge = {
  snapshot: () => invoke("snapshot"),
  add: (email) => invoke("add_account", { email }),
  remove: (accountId) => invoke("remove_account", { accountId }),
  listTemplates: (accountId) => invoke("list_templates", { accountId }),
  templateSource: (accountId) => invoke("template_source", { accountId }),
  saveTemplate: (accountId, template, isDefault) =>
    invoke("save_template", { accountId, template, isDefault }),
  deleteTemplate: (accountId, templateId) =>
    invoke("delete_template", { accountId, templateId }),
  savePreset: (accountId, sku, quantity) =>
    invoke("save_preset", { accountId, sku, quantity }),
  start: (accountId, action, params) =>
    invoke("start_task", { accountId, action, params }),
  reply: (accountId, taskId, requestId, value) =>
    invoke("reply_task", { accountId, taskId, requestId, value }),
  cancel: (accountId, taskId) => invoke("cancel_task", { accountId, taskId }),
  subscribe: async (callback) =>
    listen<Task>("task-updated", (event) => callback(event.payload)),
};
// Browser previews have no access to account data. Test injection is removed in production builds.
export const api: Bridge | undefined =
  import.meta.env.DEV && window.__DUO_KILLER_TEST__
    ? window.__DUO_KILLER_TEST__
    : isTauri()
      ? native
      : isLocalWeb
        ? localWebBridge()
        : undefined;
