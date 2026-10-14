#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod engine;
use engine::{Account, Engine, OrderTemplate, Snapshot, TaskHost, TemplateStore};
use serde_json::Value;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use tauri::{Emitter, Manager, State};

#[tauri::command]
fn snapshot(state: State<'_, Arc<Engine>>) -> Snapshot {
    state.snapshot()
}
#[tauri::command]
fn add_account(state: State<'_, Arc<Engine>>, email: String) -> Result<Account, String> {
    state.add_account(email)
}
#[tauri::command]
async fn remove_account(
    host: State<'_, TaskHost>,
    state: State<'_, Arc<Engine>>,
    account_id: String,
) -> Result<(), String> {
    state.remove_account(host.inner(), &account_id).await
}
#[tauri::command]
fn save_preset(
    state: State<'_, Arc<Engine>>,
    account_id: String,
    sku: String,
    quantity: u32,
) -> Result<Account, String> {
    state.save_preset(&account_id, sku, quantity)
}
#[tauri::command]
fn list_templates(state: State<'_, Arc<Engine>>, account_id: String) -> Result<TemplateStore, String> {
    state.list_templates(&account_id)
}
#[tauri::command]
fn template_source(state: State<'_, Arc<Engine>>, account_id: String) -> Result<Value, String> {
    state.template_source(&account_id)
}
#[tauri::command]
fn save_template(state: State<'_, Arc<Engine>>, account_id: String, template: OrderTemplate, is_default: bool) -> Result<TemplateStore, String> {
    state.save_template(&account_id, template, is_default)
}
#[tauri::command]
fn delete_template(state: State<'_, Arc<Engine>>, account_id: String, template_id: String) -> Result<TemplateStore, String> {
    state.delete_template(&account_id, &template_id)
}
#[tauri::command]
async fn start_task(
    host: State<'_, TaskHost>,
    state: State<'_, Arc<Engine>>,
    account_id: String,
    action: String,
    params: Value,
) -> Result<String, String> {
    state
        .inner()
        .start(host.inner().clone(), account_id, action, params)
}
#[tauri::command]
fn reply_task(
    host: State<'_, TaskHost>,
    state: State<'_, Arc<Engine>>,
    account_id: String,
    task_id: String,
    request_id: String,
    value: Value,
) -> Result<(), String> {
    state.reply(host.inner(), &account_id, &task_id, &request_id, value)
}
#[tauri::command]
fn cancel_task(
    state: State<'_, Arc<Engine>>,
    account_id: String,
    task_id: String,
) -> Result<(), String> {
    state.cancel(&account_id, &task_id)
}

fn main() {
    let closing = Arc::new(AtomicBool::new(false));
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let engine = Engine::new(app.path().app_data_dir()?).map_err(std::io::Error::other)?;
            let handle = app.handle().clone();
            #[cfg(debug_assertions)]
            let (worker, script) = {
                let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap();
                (
                    root.join(if cfg!(windows) {
                        ".venv/Scripts/python.exe"
                    } else {
                        ".venv/bin/python"
                    }),
                    Some(root.join("worker/main.py")),
                )
            };
            #[cfg(not(debug_assertions))]
            let (worker, script) = (
                app.path()
                    .resource_dir()?
                    .join("worker")
                    .join(if cfg!(windows) {
                        "apple-buyer-worker.exe"
                    } else {
                        "apple-buyer-worker"
                    }),
                None,
            );
            app.manage(TaskHost {
                worker,
                script,
                on_update: Arc::new(move |task| {
                    let _ = handle.emit("task-updated", task);
                }),
            });
            app.manage(engine);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            snapshot,
            add_account,
            remove_account,
            save_preset,
            list_templates,
            template_source,
            save_template,
            delete_template,
            start_task,
            reply_task,
            cancel_task
        ])
        .build(tauri::generate_context!())
        .expect("无法启动 Apple-Buyer");
    app.run(move |app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = event {
            if !closing.swap(true, Ordering::SeqCst) {
                api.prevent_exit();
                let app = app.clone();
                let engine = app.state::<Arc<Engine>>().inner().clone();
                tauri::async_runtime::spawn(async move {
                    engine.shutdown().await;
                    app.exit(0);
                });
            }
        }
    });
}
