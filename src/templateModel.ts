import type { Account, Data } from "./types";
import { selectedStoreIds, storesInCity } from "./retailStores";

export interface OrderTemplate {
  schema_version: number;
  id: string;
  revision: number;
  name: string;
  sku: string;
  quantity: number;
  max_total: string;
  mode: "HOME" | "RETAIL";
  address_source: "local" | "saved";
  saved_address: string;
  contact: {
    lastName: string;
    firstName: string;
    emailAddress: string;
    fullDaytimePhone: string;
    nationalIdLast4: string;
  };
  delivery: { state: string; city: string; district: string };
  address: { street: string; street2: string };
  shipping: {
    strategy: "cheapest" | "earliest" | "specified";
    service: string;
    max_fee: string;
  };
  pickup: {
    city: string;
    store: string;
    store_ids?: string[];
    time_mode: "earliest" | "window";
    date_from: string;
    date_to: string;
    time_from: string;
    time_to: string;
  };
  invoice: { header: string };
  wait_for_release: boolean;
  fallback_skus: string[];
}
export interface TemplateStore {
  schema_version: number;
  default_id: string;
  templates: OrderTemplate[];
}
export const emptyTemplates: TemplateStore = {
  schema_version: 1,
  default_id: "",
  templates: [],
};

export function newTemplate(account: Account): OrderTemplate {
  return {
    schema_version: 1,
    id: "",
    revision: 0,
    name: "",
    sku: account.preset_sku || "MLH74FE",
    quantity: account.preset_quantity || 1,
    max_total: "",
    mode: "HOME",
    address_source: "local",
    saved_address: "",
    contact: {
      lastName: "",
      firstName: "",
      emailAddress: account.email,
      fullDaytimePhone: "",
      nationalIdLast4: "",
    },
    delivery: { state: "", city: "", district: "" },
    address: { street: "", street2: "" },
    shipping: { strategy: "cheapest", service: "", max_fee: "0" },
    pickup: {
      city: "",
      store: "",
      store_ids: [],
      time_mode: "earliest",
      date_from: "",
      date_to: "",
      time_from: "",
      time_to: "",
    },
    invoice: { header: "个人" },
    wait_for_release: false,
    fallback_skus: [],
  };
}

export function templateErrors(t: OrderTemplate): string[] {
  const errors: string[] = [];
  const text = (v: string, label: string, pattern?: RegExp) => {
    if (!v.trim() || (pattern && !pattern.test(v.trim()))) errors.push(label);
  };
  const amount = (v: string, label: string, positive = false) => {
    if (
      !/^\d{1,9}(?:\.\d{1,2})?$/.test(v.trim()) ||
      (positive && Number(v) <= 0)
    )
      errors.push(label);
  };
  text(t.name, "模板名称");
  text(t.sku, "商品 SKU", /^[A-Z0-9]{7}(?:\/A)?$/i);
  if (!Number.isInteger(t.quantity) || t.quantity < 1 || t.quantity > 999)
    errors.push("购买数量");
  amount(t.max_total, "订单总额上限", true);
  text(t.contact.lastName, "姓氏");
  text(t.contact.firstName, "名字");
  text(t.contact.emailAddress, "通知邮箱", /^[^\s@*]+@[^\s@]+\.[^\s@]+$/);
  text(t.contact.fullDaytimePhone, "手机号", /^\d{11,12}$/);
  text(t.invoice.header, "个人电子发票抬头");
  if (t.mode === "HOME") {
    text(t.delivery.state, "省份");
    text(t.delivery.city, "城市");
    text(t.delivery.district, "区县");
    if (t.address_source === "local") text(t.address.street, "详细地址");
    else text(t.saved_address, "官网地址完整地区与街道");
    amount(t.shipping.max_fee, "运费上限");
    if (t.shipping.strategy === "specified")
      text(t.shipping.service, "指定配送服务");
  } else {
    text(t.pickup.city, "提货城市");
    const ids = selectedStoreIds(t.pickup);
    if (
      !ids.length ||
      new Set(ids).size !== ids.length ||
      ids.some((id) => !storesInCity(t.pickup.city).some((s) => s.id === id))
    )
      errors.push("当前城市的门店及顺序");
    if (
      t.contact.nationalIdLast4.trim() &&
      !/^[A-Za-z0-9]{4}$/.test(t.contact.nationalIdLast4.trim())
    )
      errors.push("证件后四位");
    if (t.pickup.time_mode === "window") {
      const p = t.pickup;
      if (
        !p.date_from ||
        !p.date_to ||
        p.date_from > p.date_to ||
        !p.time_from ||
        !p.time_to ||
        p.time_from >= p.time_to
      )
        errors.push("取货日期和时间范围");
    }
  }
  if (
    t.fallback_skus.length > 30 ||
    t.fallback_skus.some((s) => !/^[A-Z0-9]{7}(?:\/A)?$/i.test(s.trim()))
  )
    errors.push("替代 SKU 列表");
  return errors;
}

export function importTemplate(
  account: Account,
  source: Data,
  purchase: Data,
): OrderTemplate {
  const t = newTemplate(account);
  return {
    ...t,
    name: "现有配置",
    sku: purchase.sku || t.sku,
    quantity: purchase.quantity || t.quantity,
    mode: purchase.mode === "RETAIL" ? "RETAIL" : "HOME",
    wait_for_release: purchase.waitForRelease === true,
    contact: { ...t.contact, ...source.contact },
    delivery: { ...t.delivery, ...source.delivery },
    address: { ...t.address, ...source.address },
    pickup: {
      ...t.pickup,
      city: purchase.city || "",
      store: purchase.store || "",
      store_ids: purchase.store_ids || [],
    },
  };
}
