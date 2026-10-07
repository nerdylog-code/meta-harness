//! The desktop shell: a window, a child process, and nothing else.
//!
//! WP-007 asks for a shell that cannot accrete business logic and that must not grow a second
//! implementation of the daemon's lifecycle. So this file does four things and no more:
//!
//! 1. find the repository root and a Python interpreter (the daemon and the host are Python;
//!    A8 records that honestly rather than pretending the bundle is self-contained);
//! 2. spawn `apps/desktop/host/desktop_host.py`, which owns the daemon through WP-005's
//!    supervisor, and read the single JSON status line it writes;
//! 3. navigate the webview to the daemon's own URL -- served by the daemon, same origin, no
//!    CORS proxy in the middle -- or replace the boot page with the failure reason and the log
//!    path (never a blank window);
//! 4. close the host's stdin when the window goes away, which is the host's signal to kill the
//!    daemon tree and report whether the kill was clean.
//!
//! The renderer is given no capability to reach the OS (`capabilities/default.json`), and no
//! secret is ever injected into it: the web context only ever sees an http://127.0.0.1 URL.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};

const DEFAULT_PORT: u16 = 8765;

struct Host(Mutex<Option<Child>>);

fn repo_root() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("MH_REPO_ROOT") {
        let path = PathBuf::from(explicit);
        if path.join("apps/daemon/metaharness").is_dir() {
            return Some(path);
        }
    }
    // Walk up from the executable: target/release/… → the checkout it was built in.
    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors() {
            if ancestor.join("apps/daemon/metaharness").is_dir() {
                return Some(ancestor.to_path_buf());
            }
        }
    }
    // Last resort: the directory this crate was compiled in. Correct in a dev checkout, wrong in
    // a distributed bundle -- which is exactly why A8 exists.
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let candidate = manifest.parent()?.parent()?;
    candidate.join("apps/daemon/metaharness").is_dir().then(|| candidate.to_path_buf())
}

fn python_executable() -> String {
    if let Ok(explicit) = std::env::var("MH_PYTHON") {
        return explicit;
    }
    for candidate in ["python3", "python"] {
        let probe = if cfg!(windows) {
            Command::new("where").arg(candidate).output()
        } else {
            Command::new("which").arg(candidate).output()
        };
        if probe.map(|out| out.status.success()).unwrap_or(false) {
            return candidate.to_string();
        }
    }
    "python3".to_string()
}

fn port() -> u16 {
    std::env::var("MH_PORT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(DEFAULT_PORT)
}

fn show(window: &tauri::WebviewWindow, html: &str) {
    let escaped = serde_json::to_string(html).unwrap_or_else(|_| "\"\"".into());
    let _ = window.eval(&format!("document.body.innerHTML = {escaped};"));
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();
            let root = repo_root();
            let port = port();
            let window = handle
                .get_webview_window("main")
                .expect("the main window is declared in tauri.conf.json");

            let Some(root) = root else {
                show(
                    &window,
                    "<h1>Meta-Harness could not start</h1><p>This shell could not find the \
                     Meta-Harness checkout. Set MH_REPO_ROOT to the directory that contains \
                     <code>apps/daemon/metaharness</code>.</p>",
                );
                return Ok(());
            };

            let host_script = root.join("apps/desktop/host/desktop_host.py");
            if !host_script.is_file() {
                show(
                    &window,
                    &format!(
                        "<h1>Meta-Harness could not start</h1><p>Missing host script: \
                         <code>{}</code></p>",
                        host_script.display()
                    ),
                );
                return Ok(());
            }

            let mut command = Command::new(python_executable());
            command
                .arg(&host_script)
                .arg("--port")
                .arg(port.to_string())
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                const CREATE_NO_WINDOW: u32 = 0x0800_0000;
                command.creation_flags(CREATE_NO_WINDOW);
            }

            let mut child = match command.spawn() {
                Ok(child) => child,
                Err(error) => {
                    show(
                        &window,
                        &format!(
                            "<h1>Meta-Harness could not start</h1><p>The shell could not run \
                             <code>python</code>: {error}</p><p>Install Python 3.12+ and reopen \
                             the app.</p>"
                        ),
                    );
                    return Ok(());
                }
            };

            // Take the pipe out before the child moves into managed state: one owner per pipe.
            let stdout_pipe = child.stdout.take();
            app.manage(Host(Mutex::new(Some(child))));

            if let Some(pipe) = stdout_pipe {
                let reader = BufReader::new(pipe);
                let navigator = window.clone();
                std::thread::spawn(move || {
                    for line in reader.lines() {
                        let Ok(line) = line else { break };
                        let Ok(status) = serde_json::from_str::<serde_json::Value>(&line) else {
                            continue;
                        };
                        match status.get("status").and_then(|value| value.as_str()) {
                            Some("started") => {
                                let bootstrap_file = status
                                    .get("bootstrap_file")
                                    .and_then(|value| value.as_str());
                                let bootstrap_url = bootstrap_file
                                    .and_then(|path| std::fs::read_to_string(path).ok())
                                    .map(|value| value.trim().to_string())
                                    .filter(|value| {
                                        let prefixes = [
                                            format!("http://127.0.0.1:{port}/auth/bootstrap?capability="),
                                            format!("http://localhost:{port}/auth/bootstrap?capability="),
                                        ];
                                        prefixes.iter().any(|prefix| {
                                            value.strip_prefix(prefix).is_some_and(|capability| {
                                                !capability.is_empty()
                                                    && capability.bytes().all(|byte| {
                                                        byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-'
                                                    })
                                            })
                                        })
                                    });
                                if let Some(url) = bootstrap_url {
                                    let target = serde_json::to_string(&url).unwrap_or_default();
                                    let _ = navigator.eval(&format!("window.location.replace({target});"));
                                } else {
                                    show(
                                        &navigator,
                                        "<h1>Meta-Harness could not authenticate</h1>\
                                         <p>The daemon has no fresh bootstrap capability. Close this window, \
                                         stop the daemon, and relaunch Meta-Harness.</p>",
                                    );
                                }
                                return;
                            }
                            Some("attach_refused") => {
                                let reason = status
                                    .get("reason")
                                    .and_then(|value| value.as_str())
                                    .unwrap_or("The running daemon requires authentication.");
                                show(
                                    &navigator,
                                    &format!(
                                        "<h1>Meta-Harness did not attach</h1><p>{reason}</p>"
                                    ),
                                );
                                return;
                            }
                            Some("failed") => {
                                let reason = status
                                    .get("reason")
                                    .and_then(|value| value.as_str())
                                    .unwrap_or("the daemon did not start");
                                let log = status.get("log").and_then(|value| value.as_str()).unwrap_or("-");
                                show(
                                    &navigator,
                                    &format!(
                                        "<h1>Meta-Harness could not start</h1><p>{reason}</p>\
                                         <p>Log: <code>{log}</code></p>"
                                    ),
                                );
                                return;
                            }
                            _ => {}
                        }
                    }
                });
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("the shell failed to build")
        .run(|app, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(state) = app.try_state::<Host>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            // Closing stdin is the host's signal: it kills the daemon tree and
                            // checks for orphans. Dropping the handle is the shell's whole
                            // shutdown protocol.
                            drop(child.stdin.take());
                            let deadline = std::time::Instant::now() + Duration::from_secs(10);
                            while std::time::Instant::now() < deadline {
                                match child.try_wait() {
                                    Ok(Some(_)) => break,
                                    Ok(None) => std::thread::sleep(Duration::from_millis(50)),
                                    Err(_) => break,
                                }
                            }
                            let _ = child.kill();
                        }
                    }
                }
            }
        });
}
