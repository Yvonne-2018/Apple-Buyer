#[path = "../../src-tauri/src/engine.rs"]
mod engine;

use axum::{
    extract::{DefaultBodyLimit, Path as UrlPath, Request, State},
    http::{header, HeaderValue, StatusCode},
    middleware::{self, Next},
    response::{
        sse::{Event, KeepAlive},
        IntoResponse, Response, Sse,
    },
    routing::{get, post},
    Json, Router,
};
use engine::{Engine, OrderTemplate, Task, TaskHost};
use fs2::FileExt;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{convert::Infallible, fs::OpenOptions, path::PathBuf, sync::Arc};
use tokio::sync::{broadcast, watch};
use tower_http::services::ServeDir;

#[derive(Clone)]
struct LocalState {
    engine: Arc<Engine>,
    host: TaskHost,
    events: broadcast::Sender<Task>,
    stopping: watch::Receiver<bool>,
    authority: String,
    token: String,
    dev_origin: Option<String>,
}

// Match the existing desktop bridge so all account and checkout UI can be reused.
#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Input {
    #[serde(default)]
    email: String,
    #[serde(default)]
    account_id: String,
    #[serde(default)]
    action: String,
    #[serde(default)]
    params: Value,
    #[serde(default)]
    task_id: String,
    #[serde(default)]
    request_id: String,
    #[serde(default)]
    value: Value,
    #[serde(default)]
    sku: String,
    #[serde(default)]
    quantity: u32,
    #[serde(default)]
    template: OrderTemplate,
    #[serde(default)]
    template_id: String,
    #[serde(default)]
    is_default: bool,
}

async fn command(
    State(s): State<LocalState>,
    UrlPath(command): UrlPath<String>,
    Json(p): Json<Input>,
) -> Response {
    let result: Result<Value, String> = match command.as_str() {
        "snapshot" => Ok(json!(s.engine.snapshot())),
        "add_account" => s.engine.add_account(p.email).map(|a| json!(a)),
        "remove_account" => s.engine.remove_account(&s.host, &p.account_id).await.map(|_| Value::Null),
        "save_preset" => s
            .engine
            .save_preset(&p.account_id, p.sku, p.quantity)
            .map(|a| json!(a)),
        "list_templates" => s.engine.list_templates(&p.account_id).map(|v| json!(v)),
        "template_source" => s.engine.template_source(&p.account_id),
        "save_template" => s.engine.save_template(&p.account_id, p.template, p.is_default).map(|v| json!(v)),
        "delete_template" => s.engine.delete_template(&p.account_id, &p.template_id).map(|v| json!(v)),
        "start_task" => s
            .engine
            .start(s.host, p.account_id, p.action, p.params)
            .map(|id| json!(id)),
        "reply_task" => s
            .engine
            .reply(&s.host, &p.account_id, &p.task_id, &p.request_id, p.value)
            .map(|_| Value::Null),
        "cancel_task" => s
            .engine
            .cancel(&p.account_id, &p.task_id)
            .map(|_| Value::Null),
        _ => return StatusCode::NOT_FOUND.into_response(),
    };
    match result {
        Ok(value) => Json(value).into_response(),
        Err(message) => (StatusCode::BAD_REQUEST, Json(json!({"error":message}))).into_response(),
    }
}

async fn events(State(s): State<LocalState>) -> Response {
    // Subscribe before taking the snapshot; reconnects recover prompts/results missed offline.
    let mut receiver = s.events.subscribe();
    let mut stopping = s.stopping.clone();
    let stream = async_stream::stream! {
        // Flush headers immediately, including a fresh workspace with no tasks.
        yield Ok::<_, Infallible>(Event::default().comment("connected"));
        for task in s.engine.snapshot().tasks {
            yield Ok::<_, Infallible>(Event::default().json_data(task).unwrap());
        }
        loop {
            tokio::select! {
                _ = stopping.changed() => break,
                event = receiver.recv() => match event {
                    Ok(task) => yield Ok(Event::default().json_data(task).unwrap()),
                    Err(broadcast::error::RecvError::Lagged(_)) => {
                        for task in s.engine.snapshot().tasks {
                            yield Ok(Event::default().json_data(task).unwrap());
                        }
                    }
                    Err(_) => break,
                }
            }
        }
    };
    Sse::new(stream)
        .keep_alive(KeepAlive::default().interval(std::time::Duration::from_secs(5)))
        .into_response()
}

async fn guard(State(s): State<LocalState>, request: Request, next: Next) -> Response {
    // Loopback binding alone is insufficient: reject DNS rebinding and foreign browser origins.
    if *s.stopping.borrow() {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    let h = request.headers();
    let host_ok = h.get(header::HOST).and_then(|v| v.to_str().ok()) == Some(s.authority.as_str());
    let origin_ok = h.get(header::ORIGIN).is_none_or(|v| {
        v == format!("http://{}", s.authority).as_str()
            || s.dev_origin.as_deref().is_some_and(|origin| v == origin)
    });
    if !host_ok || !origin_ok {
        return StatusCode::FORBIDDEN.into_response();
    }
    if request.uri().path().starts_with("/api/")
        && h.get(header::AUTHORIZATION).and_then(|v| v.to_str().ok()) != Some(s.token.as_str())
    {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error":"连接已失效，请使用启动程序显示的链接重新打开。"})),
        )
            .into_response();
    }
    let mut response = next.run(request).await;
    let h = response.headers_mut();
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    h.insert(
        header::REFERRER_POLICY,
        HeaderValue::from_static("no-referrer"),
    );
    h.insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    h.insert(header::CONTENT_SECURITY_POLICY, HeaderValue::from_static("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'"));
    response
}

fn data_directory() -> Result<PathBuf, String> {
    let base = if cfg!(target_os = "macos") {
        PathBuf::from(std::env::var("HOME").map_err(|_| "无法确定用户目录")?)
            .join("Library/Application Support")
    } else if cfg!(windows) {
        PathBuf::from(std::env::var("APPDATA").map_err(|_| "无法确定用户目录")?)
    } else {
        return Err("当前本地 Web 构建支持 macOS 和 Windows".into());
    };
    Ok(base.join("com.niulai.local-web"))
}

fn open_browser(url: &str) -> std::io::Result<()> {
    #[cfg(target_os = "macos")]
    std::process::Command::new("open").arg(url).spawn()?;
    #[cfg(windows)]
    std::process::Command::new("rundll32.exe")
        .args(["url.dll,FileProtocolHandler", url])
        .spawn()?;
    Ok(())
}

async fn shutdown_signal() {
    #[cfg(unix)]
    {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("signal handler");
        tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = term.recv() => {} }
    }
    #[cfg(windows)]
    {
        let mut break_signal = tokio::signal::windows::ctrl_break().expect("signal handler");
        tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = break_signal.recv() => {} }
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut directory = None;
    let mut port: u16 = 17653;
    let mut open = true;
    let mut dev_origin = None;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--data-dir" => {
                directory = Some(PathBuf::from(args.next().ok_or("--data-dir 缺少路径")?))
            }
            "--port" => port = args.next().ok_or("--port 缺少端口")?.parse()?,
            "--no-open" => open = false,
            "--dev-origin" if cfg!(debug_assertions) => {
                let origin = args.next().ok_or("--dev-origin 缺少网址")?;
                let dev_port = origin
                    .strip_prefix("http://127.0.0.1:")
                    .and_then(|port| port.parse::<u16>().ok())
                    .filter(|port| *port != 0)
                    .ok_or("开发网页必须使用 http://127.0.0.1:<端口>")?;
                dev_origin = Some(format!("http://127.0.0.1:{dev_port}"));
            }
            _ => return Err(format!("未知参数：{arg}").into()),
        }
    }
    let package = std::env::current_exe()?
        .parent()
        .ok_or("无效程序路径")?
        .to_path_buf();
    let development = dev_origin.is_some();
    let (worker, script) = if development {
        let project = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .ok_or("无效项目路径")?;
        (
            project.join(if cfg!(windows) {
                ".venv/Scripts/python.exe"
            } else {
                ".venv/bin/python"
            }),
            Some(project.join("worker/main.py")),
        )
    } else {
        (
            package.join("worker").join(if cfg!(windows) {
                "duo-killer-worker.exe"
            } else {
                "duo-killer-worker"
            }),
            None,
        )
    };
    if !worker.is_file() || (!development && !package.join("www/index.html").is_file()) {
        return Err(if development {
            "请先执行 pnpm worker:setup 准备 Python 开发环境"
        } else {
            "程序文件不完整，请运行项目中的 pnpm build:web 重新构建"
        }
        .into());
    }
    let root = match directory {
        Some(path) => path,
        None if development => data_directory()?.with_file_name("com.niulai.local-web-dev"),
        None => data_directory()?,
    };
    std::fs::create_dir_all(&root)?;
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(root.join(".server.lock"))?;
    lock.try_lock_exclusive()
        .map_err(|_| "这个本地 Web 工作空间已在运行，请使用已打开的页面")?;
    let engine = Engine::new(root)?;
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port))
        .await
        .map_err(|e| format!("无法启动本地服务，端口可能被占用：{e}"))?;
    let authority = listener.local_addr()?.to_string();
    let secret = format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    );
    let url = format!("http://{authority}/#access={secret}");
    let (sender, _) = broadcast::channel(256);
    let sink = sender.clone();
    let (shutdown, stopping) = watch::channel(false);
    let state = LocalState {
        engine: engine.clone(),
        host: TaskHost {
            worker,
            script,
            on_update: Arc::new(move |task| {
                let _ = sink.send(task.clone());
            }),
        },
        events: sender,
        stopping,
        authority,
        token: format!("Bearer {secret}"),
        dev_origin,
    };
    let mut app = Router::new()
        .route("/api/events", get(events))
        .route("/api/{command}", post(command));
    if !development {
        app = app.fallback_service(ServeDir::new(package.join("www")));
    }
    let app = app
        .layer(DefaultBodyLimit::max(1_048_576))
        .layer(middleware::from_fn_with_state(state.clone(), guard))
        .with_state(state);
    println!(
        "DUO Killer · 本地 Web\n{url}\n仅限本机访问。关闭网页后服务仍运行；在此终端按 Ctrl+C 退出。"
    );
    if open && open_browser(&url).is_err() {
        eprintln!("无法自动打开默认浏览器，请打开上面的本机链接。");
    }
    let shutdown_engine = engine.clone();
    let result = axum::serve(listener, app)
        .with_graceful_shutdown(async move {
            // The dev launcher owns stdin. EOF also cleans up tasks if that parent crashes.
            let parent_closed = async {
                if development {
                    let (sender, receiver) = tokio::sync::oneshot::channel();
                    std::thread::spawn(move || {
                        let _ = std::io::stdin().read_line(&mut String::new());
                        let _ = sender.send(());
                    });
                    let _ = receiver.await;
                } else {
                    std::future::pending::<()>().await;
                }
            };
            tokio::select! { _ = shutdown_signal() => {}, _ = parent_closed => {} }
            let _ = shutdown.send(true);
            shutdown_engine.shutdown().await;
        })
        .await;
    if result.is_err() {
        engine.shutdown().await;
    }
    drop(lock);
    result?;
    Ok(())
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("启动失败：{error}");
        std::process::exit(1);
    }
}
