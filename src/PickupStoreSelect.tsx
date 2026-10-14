import { ArrowDown, ArrowUp, X } from "lucide-react";
import { SelectField } from "./SelectField";
import {
  normalizeCity,
  retailCatalog,
  retailCities,
  storesInCity,
  storeLabel,
} from "./retailStores";

export function PickupStoreSelect({
  city,
  storeIds,
  legacyStore = "",
  cityLabel = "提货城市",
  onChange,
}: {
  city: string;
  storeIds: string[];
  legacyStore?: string;
  cityLabel?: string;
  onChange: (city: string, storeIds: string[]) => void;
}) {
  const stores = storesInCity(city);
  function move(index: number, offset: number) {
    const next = [...storeIds];
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    onChange(normalizeCity(city), next);
  }
  return (
    <div className="pickup-store-select">
      <SelectField
        label={cityLabel}
        value={normalizeCity(city)}
        required
        options={retailCities.map((value) => ({ value, label: value }))}
        placeholder="选择自提城市"
        onChange={(value) => onChange(value, [])}
      />
      <p className="muted">
        Apple 中国大陆直营店 · {retailCatalog.stores.length} 家 · 更新于{" "}
        {retailCatalog.retrieved_at}
      </p>
      {legacyStore && !storeIds.length && (
        <p className="template-missing">
          原门店“{legacyStore}”未匹配目录，请从列表重新选择。
        </p>
      )}
      {city && (
        <fieldset className="pickup-store-options">
          <legend>当前城市门店（可多选）</legend>
          {stores.map((store) => (
            <label className="pickup-store-option" key={store.id}>
              <input
                type="checkbox"
                checked={storeIds.includes(store.id)}
                aria-label={`选择 Apple ${store.name}`}
                onChange={(e) =>
                  onChange(
                    normalizeCity(city),
                    e.target.checked
                      ? [...storeIds, store.id]
                      : storeIds.filter((s) => s !== store.id),
                  )
                }
              />
              <span>
                <strong>Apple {store.name}</strong>
                <small>{store.address}</small>
              </span>
            </label>
          ))}
        </fieldset>
      )}
      {!!storeIds.length && (
        <div className="pickup-store-priority" aria-label="门店尝试顺序">
          <strong>下单顺序</strong>
          <ol>
            {storeIds.map((id, index) => (
              <li key={id}>
                <span>{storeLabel(id)}</span>
                <div className="button-row">
                  <button
                    type="button"
                    aria-label={`上移 ${storeLabel(id)}`}
                    disabled={index === 0}
                    onClick={() => move(index, -1)}
                  >
                    <ArrowUp size={14} />
                  </button>
                  <button
                    type="button"
                    aria-label={`下移 ${storeLabel(id)}`}
                    disabled={index === storeIds.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    <ArrowDown size={14} />
                  </button>
                  <button
                    type="button"
                    aria-label={`移除 ${storeLabel(id)}`}
                    onClick={() =>
                      onChange(
                        normalizeCity(city),
                        storeIds.filter((s) => s !== id),
                      )
                    }
                  >
                    <X size={14} />
                  </button>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
      <p className="muted">
        按以上顺序尝试；当前门店不可选时自动切换下一家。仅使用所选门店，全部不可选时按等待到货及替代
        SKU 配置处理。
      </p>
    </div>
  );
}
