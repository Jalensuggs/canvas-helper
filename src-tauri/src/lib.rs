use serde::Deserialize;
use std::{
    fs,
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct Backend(Mutex<Option<CommandChild>>);

#[derive(Deserialize)]
struct Discovery {
    host: String,
    port: u16,
}

fn start_backend(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let app_data = app.path().app_data_dir()?;
    fs::create_dir_all(&app_data)?;
    let discovery = app_data.join("backend.json");
    let _ = fs::remove_file(&discovery);
    let (mut events, child) = app
        .shell()
        .sidecar("canvas-helper-backend")?
        .args([
            "--data-dir",
            &app_data.to_string_lossy(),
            "--discovery-file",
            &discovery.to_string_lossy(),
        ])
        .spawn()?;
    *app.state::<Backend>().0.lock().expect("backend lock poisoned") = Some(child);
    tauri::async_runtime::spawn(async move {
        while events.recv().await.is_some() {}
    });

    let deadline = Instant::now() + Duration::from_secs(20);
    let details = loop {
        if let Ok(raw) = fs::read_to_string(&discovery) {
            if let Ok(value) = serde_json::from_str::<Discovery>(&raw) {
                break value;
            }
        }
        if Instant::now() >= deadline {
            return Err("backend did not publish its port within 20 seconds".into());
        }
        thread::sleep(Duration::from_millis(100));
    };
    let url = format!("http://{}:{}/", details.host, details.port).parse()?;
    app.get_webview_window("main")
        .ok_or("main window is unavailable")?
        .navigate(url)?;
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            start_backend(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Canvas Helper desktop");

    app.run(|handle, event| match event {
        RunEvent::Resumed => {
            if let Some(window) = handle.get_webview_window("main") {
                let _ = window.eval(
                    "window.dispatchEvent(new Event('canvas-helper-resume'))",
                );
            }
        }
        RunEvent::ExitRequested { .. } | RunEvent::Exit => {
            if let Some(child) = handle
                .state::<Backend>()
                .0
                .lock()
                .expect("backend lock poisoned")
                .take()
            {
                let _ = child.kill();
            }
        }
        _ => {}
    });
}
