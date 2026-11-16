// thync on the desktop: a cloud that wanders the screen and shows what thync sees (ui/pet.js), a menu bar icon,
// and the usual thync page in its own window. The hub keeps running on its own, so the web page stays available.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

use tauri::image::Image;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_notification::NotificationExt;

/// $THYNC_HOME with a leading ~ expanded, as the hub does, else ~/.thync.
fn state_dir() -> PathBuf {
    let home = PathBuf::from(std::env::var("HOME").unwrap_or_default());
    match std::env::var("THYNC_HOME") {
        Ok(p) if p == "~" => home,
        Ok(p) => p.strip_prefix("~/").map(|rest| home.join(rest)).unwrap_or_else(|| PathBuf::from(p)),
        Err(_) => home.join(".thync"),
    }
}

fn token() -> Option<String> {
    std::fs::read_to_string(state_dir().join("token"))
        .ok()
        .map(|t| t.trim().to_string())
        .filter(|t| !t.is_empty())
}

/// The hub's address: 127.0.0.1 on the `port` the hub reads from config.json, 8765 when unset (or the file is
/// missing or not JSON, as for the hub). A `port` that cannot be read is an error, not 8765: whatever else listens
/// there must not get the token.
fn hub() -> Result<String, String> {
    let config = std::fs::read_to_string(state_dir().join("config.json"))
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok());
    port_of(config.as_ref())
        .map(|p| format!("http://127.0.0.1:{p}"))
        .ok_or_else(|| "config.json 의 port 를 읽지 못했어요".to_string())
}

/// `port` as the hub reads it (`int(...)`): a number or a numeric string; 8765 when the key is absent. A config the
/// hub cannot start with (`"port": null`, a root that is not an object) gives None.
fn port_of(config: Option<&serde_json::Value>) -> Option<u16> {
    use serde_json::Value;
    let Some(config) = config else { return Some(8765) };
    match config.as_object()?.get("port") {
        None => Some(8765),
        Some(Value::Number(n)) => n.as_u64().and_then(|p| u16::try_from(p).ok()),
        Some(Value::String(s)) => s.trim().parse().ok(),
        Some(_) => None,
    }
}

/// The thync checkout this app was built from; used to start the hub when there is no ~/.thync/run.sh.
fn repo_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// What the cloud shows: GET /status.json from the hub. Done here, not in the page, so the token stays out of it.
#[tauri::command]
fn status() -> Result<serde_json::Value, String> {
    let token = token().ok_or("thync 토큰이 없어요")?;
    let agent = ureq::AgentBuilder::new().timeout(Duration::from_secs(3)).build();
    agent
        .get(&format!("{}/status.json", hub()?))
        .set("X-Thync-Token", &token)
        .call()
        .map_err(|e| e.to_string())?
        .into_json::<serde_json::Value>()
        .map_err(|e| e.to_string())
}

/// Hands an obsidian:// link (a note, the Kanban board) to Obsidian, which the webview cannot open by itself.
/// Returns whether it was one.
fn open_in_obsidian(url: &tauri::Url) -> bool {
    if url.scheme() != "obsidian" {
        return false;
    }
    let _ = Command::new("/usr/bin/open")
        .arg(url.as_str())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
    true
}

fn show_main(app: &AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("main") {
        return w.show().and_then(|_| w.unminimize()).and_then(|_| w.set_focus()).map_err(|e| e.to_string());
    }
    let url: tauri::Url = format!("{}/#t={}", hub()?, token().unwrap_or_default()).parse().map_err(|e| format!("{e}"))?;
    let w = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
        .title("thync")
        .inner_size(1400.0, 900.0)
        // A board card sets location.href; a note link in the detail panel calls window.open.
        .on_navigation(|url| !open_in_obsidian(url))
        .on_new_window(|url, _| {
            if open_in_obsidian(&url) {
                tauri::webview::NewWindowResponse::Deny
            } else {
                tauri::webview::NewWindowResponse::Allow
            }
        })
        .build()
        .map_err(|e| e.to_string())?;
    w.set_focus().map_err(|e| e.to_string())
}

#[tauri::command]
fn open_thync(app: AppHandle) -> Result<(), String> {
    show_main(&app)
}

/// A macOS notification and a short sound, for a question waiting on me or a long run that just ended.
#[tauri::command]
fn notify(app: AppHandle, title: String, body: String) {
    let _ = app.notification().builder().title(title).body(body).show();
    let _ = Command::new("/usr/bin/afplay")
        .arg("/System/Library/Sounds/Glass.aiff")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}

/// Start the hub in the background, through a login shell so it finds the same python3 and gh as the terminal.
/// ~/.thync/run.sh wins when it exists (it may pick a GitHub account); the hub outlives this app either way.
#[tauri::command]
fn start_hub() -> Result<(), String> {
    let run = state_dir().join("run.sh");
    let mut cmd = Command::new("/bin/zsh");
    if run.exists() {
        cmd.arg("-lc").arg("sh \"$0\"").arg(run);
    } else {
        let log = state_dir().join("server.log");
        cmd.arg("-lc")
            .arg("nohup python3 -u -m thync >> \"$0\" 2>&1 < /dev/null &")
            .arg(log)
            .current_dir(repo_dir());
    }
    cmd.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![status, open_thync, notify, start_hub])
        .setup(|app| {
            // Lives in the menu bar, not the Dock.
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            let open = MenuItem::with_id(app, "open", "thync 열기", true, None::<&str>)?;
            let toggle = MenuItem::with_id(app, "toggle", "구름 숨기기 / 보이기", true, None::<&str>)?;
            let hub = MenuItem::with_id(app, "hub", "허브 켜기", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "종료", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &toggle, &hub, &quit])?;
            TrayIconBuilder::with_id("tray")
                .icon(Image::from_bytes(include_bytes!("../icons/tray.png"))?)
                .icon_as_template(true)
                .tooltip("thync")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        let _ = show_main(app);
                    }
                    "toggle" => {
                        if let Some(w) = app.get_webview_window("pet") {
                            let _ = if w.is_visible().unwrap_or(true) { w.hide() } else { w.show() };
                        }
                    }
                    "hub" => {
                        let _ = start_hub();
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("thync desktop failed to start");
    app.run(|_, event| {
        // Closing the thync window or hiding the cloud keeps the app in the menu bar; only 종료 quits.
        if let RunEvent::ExitRequested { code: None, api, .. } = event {
            api.prevent_exit();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::port_of;
    use serde_json::json;

    #[test]
    fn port_matches_the_hub() {
        assert_eq!(port_of(None), Some(8765));
        assert_eq!(port_of(Some(&json!({}))), Some(8765));
        assert_eq!(port_of(Some(&json!({"port": 8766}))), Some(8766));
        assert_eq!(port_of(Some(&json!({"port": " 8766 "}))), Some(8766));
        assert_eq!(port_of(Some(&json!({"port": 70000}))), None);
        assert_eq!(port_of(Some(&json!({"port": "abc"}))), None);
        assert_eq!(port_of(Some(&json!({"port": [8766]}))), None);
        assert_eq!(port_of(Some(&json!({"port": null}))), None);
        assert_eq!(port_of(Some(&json!([]))), None);
    }
}
