import { useEffect, useState } from "react";
import { ArrowRight, Check, Copy, Pencil, Plus, Trash2 } from "lucide-react";
import type { Bridge } from "./api";
import type { Account, Data } from "./types";
import { presetProducts } from "./products";
import { SelectField } from "./SelectField";
import { PickupStoreSelect } from "./PickupStoreSelect";
import { selectedStoreIds, storeLabel } from "./retailStores";
import {
  emptyTemplates,
  importTemplate,
  newTemplate,
  templateErrors,
  type OrderTemplate,
} from "./templateModel";

export function OrderTemplates({
  account,
  bridge,
  busy,
  purchase,
  onRun,
  onTemplateViewChange,
}: {
  account: Account;
  bridge: Bridge;
  busy: boolean;
  purchase: Data;
  onRun: (templateId: string) => Promise<void>;
  onTemplateViewChange: (preferTemplates: boolean) => void;
}) {
  const [store, setStore] = useState(emptyTemplates);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<OrderTemplate | null>(null);
  const [isDefault, setIsDefault] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [editorVersion, setEditorVersion] = useState(0);
  const editing = !!draft;
  useEffect(() => {
    onTemplateViewChange(store.templates.length > 0 || editing);
  }, [store.templates.length, editing, onTemplateViewChange]);
  useEffect(() => {
    let disposed = false;
    bridge
      .listTemplates(account.id)
      .then((next) => {
        if (disposed) return;
        setStore(next);
        setSelectedId(next.default_id || next.templates[0]?.id || "");
      })
      .catch((e) => {
        if (!disposed) setError(String(e));
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [account.id, bridge]);
  const selected = store.templates.find((t) => t.id === selectedId);
  const errors = selected ? templateErrors(selected) : [];
  function edit(value: OrderTemplate, defaultValue = false) {
    setEditorVersion((v) => v + 1);
    const draft = structuredClone(value);
    const ids = selectedStoreIds(draft.pickup);
    draft.pickup = {
      ...draft.pickup,
      store_ids: ids,
      store: ids[0] || draft.pickup.store,
    };
    setDraft(draft);
    setIsDefault(defaultValue);
    setError("");
    setStatus("");
    setDeleting(false);
  }
  async function fromExisting() {
    setSaving(true);
    setError("");
    try {
      edit(
        importTemplate(
          account,
          await bridge.templateSource(account.id),
          purchase,
        ),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="order-templates" aria-label="下单模板">
      <div className="template-heading">
        <div>
          <h3>下单模板</h3>
          <p className="muted">提前配好，点击一次执行到付款二维码。</p>
        </div>
        <div className="button-row">
          <button
            type="button"
            disabled={loading || saving}
            onClick={() => edit(newTemplate(account))}
          >
            <Plus size={14} />
            新建模板
          </button>
          <button
            type="button"
            disabled={loading || saving}
            onClick={fromExisting}
          >
            从现有配置创建
          </button>
        </div>
      </div>
      {loading ? (
        <p role="status">正在读取下单模板…</p>
      ) : store.templates.length > 0 ? (
        <>
          <SelectField
            label="选择下单模板"
            value={selectedId}
            disabled={saving}
            onChange={(id) => {
              setSelectedId(id);
              setDeleting(false);
              setStatus("");
            }}
            options={store.templates.map((t) => ({
              value: t.id,
              label: t.name + (t.id === store.default_id ? " · 默认" : ""),
            }))}
          />
          {selected && (
            <div className="template-summary">
              <div className="template-summary-heading">
                <strong>{selected.name}</strong>
                <span className={errors.length ? "muted" : "template-ready"}>
                  {errors.length ? (
                    "待补配置"
                  ) : (
                    <>
                      <Check size={14} />
                      可下单
                    </>
                  )}
                </span>
              </div>
              <p>
                {selected.sku} × {selected.quantity} ·{" "}
                {selected.mode === "HOME" ? "送货上门" : "门店自提"} · 支付宝
              </p>
              <p>
                {selected.mode === "HOME"
                  ? selected.address_source === "local"
                    ? `${selected.delivery.state} ${selected.delivery.city} ${selected.delivery.district} ${selected.address.street} ${selected.address.street2}`
                    : `官网地址：${selected.saved_address}`
                  : `${selected.pickup.city} · ${selectedStoreIds(selected.pickup).map(storeLabel).join(" → ") || selected.pickup.store} · ${selected.pickup.time_mode === "earliest" ? "最早可取时段" : `${selected.pickup.date_from} 至 ${selected.pickup.date_to}，${selected.pickup.time_from}–${selected.pickup.time_to}`}`}
              </p>
              <p>
                <strong>总额上限 ¥{selected.max_total || "未设置"}</strong>
                （含运费）
                {selected.mode === "HOME"
                  ? ` · 运费不超过 ¥${selected.shipping.max_fee}`
                  : ""}
              </p>
              <p className="muted">
                {selected.wait_for_release
                  ? "等待上架 / 到货"
                  : "按当前可购买状态执行"}
                {selected.fallback_skus.length
                  ? ` · 仅允许替换为 ${selected.fallback_skus.join("、")}`
                  : " · 仅购买指定 SKU"}
              </p>
              {errors.length > 0 && (
                <p className="template-missing">需补齐：{errors.join("、")}</p>
              )}
              <div className="button-row">
                <button
                  className="primary"
                  type="button"
                  disabled={busy || saving || !!draft || errors.length > 0}
                  onClick={() => void onRun(selected.id)}
                >
                  按模板一键下单
                  <ArrowRight size={15} />
                </button>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() =>
                    edit(selected, selected.id === store.default_id)
                  }
                >
                  <Pencil size={14} />
                  编辑
                </button>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() =>
                    edit({
                      ...selected,
                      id: "",
                      revision: 0,
                      name: `${selected.name} 副本`,
                    })
                  }
                >
                  <Copy size={14} />
                  复制
                </button>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => setDeleting(true)}
                >
                  <Trash2 size={14} />
                  删除
                </button>
              </div>
              <p className="muted">
                点击后购物袋仅保留本次目标商品，按模板自动创建未付款订单，不再逐步确认；完成后由你扫码付款。
              </p>
              {deleting && (
                <div
                  className="template-delete"
                  role="group"
                  aria-label="确认删除模板"
                >
                  <span>删除“{selected.name}”？</span>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={async () => {
                      setSaving(true);
                      setError("");
                      try {
                        const next = await bridge.deleteTemplate(
                          account.id,
                          selected.id,
                        );
                        setStore(next);
                        setSelectedId(
                          next.default_id || next.templates[0]?.id || "",
                        );
                        setDeleting(false);
                        setStatus("模板已删除");
                      } catch (e) {
                        setError(String(e));
                      } finally {
                        setSaving(false);
                      }
                    }}
                  >
                    确认删除模板
                  </button>
                  <button type="button" onClick={() => setDeleting(false)}>
                    取消删除
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      ) : (
        <p className="muted">
          还没有下单模板。新建模板或导入当前账号的购买、地址预设。
        </p>
      )}
      {draft && (
        <TemplateEditor
          key={editorVersion}
          value={draft}
          isDefault={isDefault}
          setDefault={setIsDefault}
          saving={saving}
          onChange={setDraft}
          onCancel={() => setDraft(null)}
          onSave={async () => {
            setSaving(true);
            setError("");
            try {
              const next = await bridge.saveTemplate(
                account.id,
                draft,
                isDefault,
              );
              setStore(next);
              setSelectedId(draft.id || next.templates.at(-1)?.id || "");
              setDraft(null);
              setStatus(
                templateErrors(draft).length
                  ? "草稿已保存，补齐配置后可一键下单"
                  : "模板已保存",
              );
            } catch (e) {
              setError(String(e));
            } finally {
              setSaving(false);
            }
          }}
        />
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {status && (
        <p className="template-status" role="status">
          {status}
        </p>
      )}
    </div>
  );
}

function TemplateEditor({
  value: t,
  isDefault,
  setDefault,
  saving,
  onChange,
  onCancel,
  onSave,
}: {
  value: OrderTemplate;
  isDefault: boolean;
  setDefault: (value: boolean) => void;
  saving: boolean;
  onChange: (value: OrderTemplate) => void;
  onCancel: () => void;
  onSave: () => Promise<void>;
}) {
  const set = <K extends keyof OrderTemplate>(
    key: K,
    value: OrderTemplate[K],
  ) => onChange({ ...t, [key]: value });
  const group = <
    K extends
      | "contact"
      | "delivery"
      | "address"
      | "shipping"
      | "pickup"
      | "invoice",
  >(
    key: K,
    field: keyof OrderTemplate[K],
    value: string,
  ) => set(key, { ...t[key], [field]: value });
  const field = (
    label: string,
    value: string,
    change: (v: string) => void,
    type = "text",
    placeholder = "",
  ) => (
    <label key={label}>
      {label}
      <input
        type={type}
        value={value}
        maxLength={500}
        placeholder={placeholder}
        onChange={(e) => change(e.target.value)}
      />
    </label>
  );
  const errors = templateErrors(t);
  return (
    <form
      className="template-editor"
      aria-label="下单模板编辑"
      onSubmit={(e) => {
        e.preventDefault();
        void onSave();
      }}
    >
      <h3>{t.id ? "编辑下单模板" : "新建下单模板"}</h3>
      <fieldset disabled={saving}>
        <legend>商品与金额</legend>
        {field(
          "模板名称",
          t.name,
          (v) => set("name", v),
          "text",
          "例如：抛光布 · 家里收货",
        )}
        <SelectField
          label="模板预制商品"
          value={
            presetProducts.some((p) => p.sku === t.sku.replace(/\/A$/i, ""))
              ? t.sku.replace(/\/A$/i, "")
              : ""
          }
          onChange={(v) => set("sku", v)}
          options={[
            { value: "", label: "自定义 SKU" },
            ...presetProducts.map((p) => ({
              value: p.sku,
              label: `${p.name} · ${p.sku}`,
            })),
          ]}
        />
        <div className="template-fields">
          {field("模板 SKU", t.sku, (v) => set("sku", v.toUpperCase()))}
          {field(
            "模板数量",
            String(t.quantity),
            (v) => set("quantity", Number(v)),
            "number",
          )}
          {field(
            "订单总额上限（元，含运费）",
            t.max_total,
            (v) => set("max_total", v),
            "text",
            "必须填写大于零的金额",
          )}
          <SelectField
            label="模板收货方式"
            value={t.mode}
            onChange={(v) => {
              onChange({
                ...t,
                mode: v as OrderTemplate["mode"],
                fallback_skus: v === "HOME" ? [] : t.fallback_skus,
              });
            }}
            options={[
              { value: "HOME", label: "送货上门" },
              { value: "RETAIL", label: "门店自提" },
            ]}
          />
        </div>
      </fieldset>
      <fieldset disabled={saving}>
        <legend>
          {t.mode === "HOME" ? "送货地址与服务" : "门店与取货时间"}
        </legend>
        {t.mode === "HOME" ? (
          <>
            <SelectField
              label="模板地址来源"
              value={t.address_source}
              onChange={(v) =>
                set("address_source", v as OrderTemplate["address_source"])
              }
              options={[
                { value: "local", label: "使用模板填写的地址" },
                { value: "saved", label: "指定官网已保存地址" },
              ]}
            />
            <div className="template-fields">
              {field("模板省 / 直辖市", t.delivery.state, (v) =>
                group("delivery", "state", v),
              )}
              {field("模板城市", t.delivery.city, (v) =>
                group("delivery", "city", v),
              )}
              {field("模板区 / 县", t.delivery.district, (v) =>
                group("delivery", "district", v),
              )}
            </div>
            {t.address_source === "local" ? (
              <>
                {field("模板详细地址", t.address.street, (v) =>
                  group("address", "street", v),
                )}
                {field("模板地址补充（选填）", t.address.street2, (v) =>
                  group("address", "street2", v),
                )}
              </>
            ) : (
              <>
                {field(
                  "官网地址完整地区与街道",
                  t.saved_address,
                  (v) => set("saved_address", v),
                  "text",
                  "按官网显示填写省市区和完整街道地址",
                )}
                <p className="muted">
                  执行时精确匹配当前账号的官网地址；未找到或出现多个匹配会停止。
                </p>
              </>
            )}
            <div className="template-fields">
              <SelectField
                label="模板配送服务"
                value={t.shipping.strategy}
                onChange={(v) => group("shipping", "strategy", v)}
                options={[
                  { value: "cheapest", label: "最低运费" },
                  { value: "earliest", label: "最早送达" },
                  { value: "specified", label: "指定配送服务" },
                ]}
              />
              {field("运费上限（元）", t.shipping.max_fee, (v) =>
                group("shipping", "max_fee", v),
              )}
            </div>
            {t.shipping.strategy === "specified" &&
              field("指定配送服务名称或代码", t.shipping.service, (v) =>
                group("shipping", "service", v),
              )}
          </>
        ) : (
          <>
            <PickupStoreSelect
              cityLabel="模板提货城市"
              city={t.pickup.city}
              storeIds={selectedStoreIds(t.pickup)}
              legacyStore={t.pickup.store}
              onChange={(city, store_ids) =>
                set("pickup", {
                  ...t.pickup,
                  city,
                  store_ids,
                  store: store_ids[0] || "",
                })
              }
            />
            <SelectField
              label="模板取货时间"
              value={t.pickup.time_mode}
              onChange={(v) => group("pickup", "time_mode", v)}
              options={[
                { value: "earliest", label: "最早可取时段" },
                { value: "window", label: "限定日期与时间范围" },
              ]}
            />
            {t.pickup.time_mode === "window" && (
              <div className="template-fields">
                {field(
                  "取货开始日期",
                  t.pickup.date_from,
                  (v) => group("pickup", "date_from", v),
                  "date",
                )}
                {field(
                  "取货结束日期",
                  t.pickup.date_to,
                  (v) => group("pickup", "date_to", v),
                  "date",
                )}
                {field(
                  "每天最早取货时间",
                  t.pickup.time_from,
                  (v) => group("pickup", "time_from", v),
                  "time",
                )}
                {field(
                  "每天最晚取货时间",
                  t.pickup.time_to,
                  (v) => group("pickup", "time_to", v),
                  "time",
                )}
              </div>
            )}
          </>
        )}
      </fieldset>
      <fieldset disabled={saving}>
        <legend>{t.mode === "HOME" ? "收件人和发票" : "提货人和发票"}</legend>
        <div className="template-fields">
          {field("模板姓氏", t.contact.lastName, (v) =>
            group("contact", "lastName", v),
          )}
          {field("模板名字", t.contact.firstName, (v) =>
            group("contact", "firstName", v),
          )}
          {field("模板通知邮箱", t.contact.emailAddress, (v) =>
            group("contact", "emailAddress", v),
          )}
          {field(
            "模板手机号",
            t.contact.fullDaytimePhone,
            (v) => group("contact", "fullDaytimePhone", v),
            "tel",
          )}
          {t.mode === "RETAIL" &&
            field("模板证件后四位（选填）", t.contact.nationalIdLast4, (v) =>
              group("contact", "nationalIdLast4", v.toUpperCase()),
            )}
          {field("个人电子发票抬头", t.invoice.header, (v) =>
            group("invoice", "header", v),
          )}
        </div>
        {t.mode === "RETAIL" && (
          <p className="muted">
            官网要求证件后四位时会使用模板值，未填写会停止并提示补齐。
          </p>
        )}
        <p className="muted">
          支付方式：支付宝。账号密码和临时验证码通过账号登录流程处理。
        </p>
      </fieldset>
      <fieldset disabled={saving}>
        <legend>执行选项</legend>
        <label className="template-checkbox">
          <input
            type="checkbox"
            checked={t.wait_for_release}
            onChange={(e) => set("wait_for_release", e.target.checked)}
          />
          等待上架 / 到货，每轮查询结束后 2 秒刷新，直到可购买或取消
        </label>
        {t.mode === "RETAIL" && (
          <label>
            允许的替代 SKU（选填，按顺序优先）
            <textarea
              defaultValue={t.fallback_skus.join("\n")}
              rows={3}
              placeholder="每行一个 SKU；留空则只购买原 SKU"
              onChange={(e) =>
                set(
                  "fallback_skus",
                  e.target.value
                    .split(/[\s,，;；]+/)
                    .filter(Boolean)
                    .map((v) => v.toUpperCase()),
                )
              }
            />
            <span className="muted">
              仅在所选门店全部不可取货时尝试同机型白名单配置，每个配置按门店顺序尝试，数量和金额上限不变。
            </span>
          </label>
        )}
        <label className="template-checkbox">
          <input
            type="checkbox"
            checked={isDefault}
            onChange={(e) => setDefault(e.target.checked)}
          />
          设为此账号默认模板
        </label>
      </fieldset>
      {errors.length > 0 && (
        <p className="template-missing" role="status">
          可先保存草稿。需补齐：{errors.join("、")}
        </p>
      )}
      <div className="button-row">
        <button
          className="primary"
          type="submit"
          disabled={saving || !t.name.trim()}
        >
          {saving ? "保存中…" : "保存下单模板"}
        </button>
        <button type="button" disabled={saving} onClick={onCancel}>
          取消编辑
        </button>
      </div>
    </form>
  );
}
