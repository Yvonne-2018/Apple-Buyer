import catalog from "../worker/data/apple-retail-cn.json";

export const retailCatalog = catalog;
export const retailStores = catalog.stores;
export const retailCities = [...new Set(retailStores.map((s) => s.city))].sort(
  (a, b) => a.localeCompare(b, "zh-CN"),
);
export const normalizeCity = (city: string) => city.trim().replace(/市$/, "");
export const storesInCity = (city: string) =>
  retailStores.filter((s) => s.city === normalizeCity(city));
export const storeLabel = (id: string) => {
  const store = retailStores.find((s) => s.id === id);
  return store ? `Apple ${store.name}` : id;
};

// 旧版单门店配置按编号或精确名称迁移；无法识别时保留原值，交给用户重新选择。
export function selectedStoreIds(pickup: {
  city: string;
  store: string;
  store_ids?: unknown;
}): string[] {
  if (
    Array.isArray(pickup.store_ids) &&
    pickup.store_ids.length &&
    pickup.store_ids.every((s) => typeof s === "string")
  )
    return [...pickup.store_ids];
  const key = (v: string) =>
    v
      .replace(/^Apple\s*/i, "")
      .replace(/\s/g, "")
      .toLowerCase();
  const match = storesInCity(pickup.city).filter(
    (s) => key(s.id) === key(pickup.store) || key(s.name) === key(pickup.store),
  );
  return match.length === 1 ? [match[0].id] : [];
}
