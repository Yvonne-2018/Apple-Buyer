use serde::{Deserialize, Serialize};
use std::{fs, path::Path};
use uuid::Uuid;

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Contact {
    #[serde(rename = "lastName")]
    pub last_name: String,
    #[serde(rename = "firstName")]
    pub first_name: String,
    #[serde(rename = "emailAddress")]
    pub email: String,
    #[serde(rename = "fullDaytimePhone")]
    pub phone: String,
    #[serde(rename = "nationalIdLast4")]
    pub id_last_four: String,
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Region {
    pub state: String,
    pub city: String,
    pub district: String,
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Address {
    pub street: String,
    pub street2: String,
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Shipping {
    pub strategy: String,
    pub service: String,
    pub max_fee: String,
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Pickup {
    pub city: String,
    pub store: String,
    pub store_ids: Vec<String>,
    pub time_mode: String,
    pub date_from: String,
    pub date_to: String,
    pub time_from: String,
    pub time_to: String,
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Invoice {
    pub header: String,
}

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct OrderTemplate {
    pub schema_version: u32,
    pub id: String,
    pub revision: u64,
    pub name: String,
    pub sku: String,
    pub quantity: u32,
    pub max_total: String,
    pub mode: String,
    pub address_source: String,
    // Match a current saved address by its complete region and street, never a cached session ID.
    pub saved_address: String,
    pub contact: Contact,
    pub delivery: Region,
    pub address: Address,
    pub shipping: Shipping,
    pub pickup: Pickup,
    pub invoice: Invoice,
    pub wait_for_release: bool,
    // An explicit ordered allowlist; empty means the SKU must not change.
    pub fallback_skus: Vec<String>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TemplateStore {
    pub schema_version: u32,
    pub default_id: String,
    pub templates: Vec<OrderTemplate>,
}
impl Default for TemplateStore {
    fn default() -> Self {
        Self { schema_version: 1, default_id: String::new(), templates: vec![] }
    }
}
impl TemplateStore {
    pub fn read(path: &Path) -> Result<Self, String> {
        if !path.exists() { return Ok(Self::default()); }
        let bytes = fs::read(path).map_err(|_| "无法读取下单模板")?;
        if bytes.len() > 5_000_000 { return Err("下单模板文件过大".into()); }
        let store: Self = serde_json::from_slice(&bytes).map_err(|_| "下单模板文件格式损坏，请保留文件并检查")?;
        if store.schema_version != 1 || store.templates.iter().any(|t| t.schema_version != 1) {
            return Err("下单模板版本不兼容，请使用对应版本的应用".into());
        }
        Ok(store)
    }

    pub fn save(&mut self, path: &Path, mut template: OrderTemplate, is_default: bool) -> Result<(), String> {
        if template.schema_version != 1 { return Err("下单模板版本不兼容".into()); }
        if serde_json::to_vec(&template).map_err(|_| "模板格式不正确")?.len() > 40_000
            || template.name.trim().is_empty() || template.name.len() > 200
            || template.fallback_skus.len() > 30 || template.quantity > 999 {
            return Err("请填写模板名称，数量不超过 999，替代 SKU 不超过 30 个".into());
        }
        template.name = template.name.trim().to_owned();
        template.sku = template.sku.trim().to_ascii_uppercase();
        template.fallback_skus = template.fallback_skus.iter().map(|s| s.trim().to_ascii_uppercase()).collect();
        if template.id.is_empty() {
            if self.templates.len() >= 100 { return Err("每个账号最多保存 100 个下单模板".into()); }
            template.id = Uuid::new_v4().to_string();
            template.revision = 1;
            self.templates.push(template.clone());
        } else {
            Uuid::parse_str(&template.id).map_err(|_| "模板标识无效")?;
            let current = self.templates.iter_mut().find(|t| t.id == template.id).ok_or("模板不存在或不属于此账号")?;
            if current.revision != template.revision { return Err("模板已在其他页面更新，请重新加载后编辑".into()); }
            template.revision = current.revision.checked_add(1).ok_or("模板版本无效")?;
            *current = template.clone();
        }
        if is_default { self.default_id = template.id.clone(); }
        else if self.default_id == template.id { self.default_id.clear(); }
        super::private_json(path, self)
    }

    pub fn delete(&mut self, path: &Path, id: &str) -> Result<(), String> {
        if !self.templates.iter().any(|t| t.id == id) { return Err("模板不存在或不属于此账号".into()); }
        self.templates.retain(|t| t.id != id);
        if self.default_id == id { self.default_id.clear(); }
        super::private_json(path, self)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn draft() -> OrderTemplate {
        serde_json::from_value(serde_json::json!({"schema_version":1,"name":"测试模板","sku":"mlh74fe","quantity":1})).unwrap()
    }

    #[test]
    fn revisions_defaults_and_deleted_templates_persist_without_overwriting_stale_edits() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("templates.json");
        let mut store = TemplateStore::read(&path).unwrap();
        store.save(&path, draft(), true).unwrap();
        let old = store.templates[0].clone();
        assert_eq!(old.revision, 1);
        assert_eq!(old.sku, "MLH74FE");
        let mut edit = old.clone();
        edit.name = "修改后模板".into();
        store.save(&path, edit, true).unwrap();
        assert!(store.save(&path, old.clone(), true).is_err());
        let restored = TemplateStore::read(&path).unwrap();
        assert_eq!(restored.templates[0].name, "修改后模板");
        assert_eq!(restored.templates[0].revision, 2);
        assert_eq!(old.name, "测试模板"); // A captured task snapshot stays unchanged.
        store.delete(&path, &old.id).unwrap();
        assert!(TemplateStore::read(&path).unwrap().templates.is_empty());
        assert!(store.default_id.is_empty());
    }

    #[test]
    fn unsupported_files_and_credential_fields_are_rejected() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("templates.json");
        fs::write(&path, br#"{"schema_version":2,"default_id":"","templates":[]}"#).unwrap();
        assert!(TemplateStore::read(&path).is_err());
        assert!(serde_json::from_str::<OrderTemplate>(r#"{"schema_version":1,"password":"must-not-save"}"#).is_err());
    }

    #[test]
    fn pickup_order_round_trips_and_old_single_store_defaults_to_empty_list() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("templates.json");
        let mut template = draft();
        assert!(template.pickup.store_ids.is_empty());
        template.pickup.city = "成都".into();
        template.pickup.store_ids = vec!["R580".into(), "R502".into()];
        let mut store = TemplateStore::default();
        store.save(&path, template, true).unwrap();
        assert_eq!(TemplateStore::read(&path).unwrap().templates[0].pickup.store_ids, ["R580", "R502"]);
    }
}
