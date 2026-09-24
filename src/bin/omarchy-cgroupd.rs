//! cgroup v2 hardware constraint daemon.

use inotify::{Inotify, WatchMask};
use serde::Deserialize;
use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tracing::{error, info};

const LIVE_CONFIG_DIR: &str = "/etc/omarchy-srv/live";
const LIVE_CONFIG_FILE: &str = "/etc/omarchy-srv/live/agent.toml";
const CGROUP_BASE: &str = "/sys/fs/cgroup";

#[derive(Debug, Deserialize, Clone)]
struct AgentConfig {
    agent: AgentMetadata,
    resources: ResourceAllocation,
}

#[derive(Debug, Deserialize, Clone)]
struct AgentMetadata {
    name: String,
    runtime: String,
    model: String,
}

#[derive(Debug, Deserialize, Clone)]
struct ResourceAllocation {
    memory_floor_gb: u64,
    memory_low_gb: Option<u64>,
    memory_high_gb: u64,
    memory_max_gb: Option<u64>,
    numa_nodes: Vec<u32>,
}

impl ResourceAllocation {
    fn effective_low(&self) -> u64 {
        self.memory_low_gb.unwrap_or(self.memory_floor_gb + 4)
    }
    fn effective_max(&self) -> u64 {
        self.memory_max_gb.unwrap_or(self.memory_high_gb + 4)
    }
}

#[derive(Debug, thiserror::Error)]
enum DaemonError {
    #[error("config parse error: {0}")]
    Parse(#[from] toml::de::Error),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid configuration: {0}")]
    Validation(String),
}

struct CgroupDaemon {
    inotify: Inotify,
    cgroup_base: PathBuf,
}

impl CgroupDaemon {
    fn new() -> Result<Self, DaemonError> {
        let cgroup_base = PathBuf::from(CGROUP_BASE);
        if !cgroup_base.exists() {
            return Err(DaemonError::Validation(format!(
                "cgroup v2 not mounted at {CGROUP_BASE}"
            )));
        }
        Ok(Self {
            inotify: Inotify::init()?,
            cgroup_base,
        })
    }

    fn watch_config(&mut self) -> Result<(), DaemonError> {
        self.inotify.watches().add(
            LIVE_CONFIG_DIR,
            WatchMask::MOVED_TO | WatchMask::MODIFY | WatchMask::CLOSE_WRITE,
        )?;
        info!("watching {LIVE_CONFIG_DIR} for configuration changes");
        Ok(())
    }

    fn run(&mut self, shutdown: Arc<AtomicBool>) -> Result<(), DaemonError> {
        let mut buffer = [0_u8; 4096];
        while !shutdown.load(Ordering::Relaxed) {
            let events = self.inotify.read_events(&mut buffer)?;
            let names = events
                .filter_map(|event| event.name.map(OsStr::to_owned))
                .collect::<Vec<_>>();
            for name in names {
                self.handle_event_name(&name);
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        Ok(())
    }

    fn handle_event_name(&self, name: &OsStr) {
        if name != OsStr::new("agent.toml") {
            return;
        }
        match self.process_config() {
            Ok(()) => info!("config applied successfully"),
            Err(e) => {
                error!("failed to apply config: {e}");
                self.notify_residual_error(&e.to_string());
            }
        }
    }

    fn process_config(&self) -> Result<(), DaemonError> {
        let content = fs::read_to_string(LIVE_CONFIG_FILE)?;
        let config: AgentConfig = toml::from_str(&content)?;
        self.validate_config(&config)?;
        self.apply_to_cgroup(&config)
    }

    fn validate_config(&self, config: &AgentConfig) -> Result<(), DaemonError> {
        if config.agent.name.trim().is_empty()
            || config.agent.runtime.trim().is_empty()
            || config.agent.model.trim().is_empty()
        {
            return Err(DaemonError::Validation(
                "agent name, runtime, and model must be non-empty".to_string(),
            ));
        }
        if config.agent.name.contains('/')
            || matches!(config.agent.name.as_str(), "." | "..")
        {
            return Err(DaemonError::Validation(
                "agent name must be a safe single cgroup component".to_string(),
            ));
        }

        let res = &config.resources;
        let low = res.effective_low();
        let max = res.effective_max();
        if !(res.memory_floor_gb < low && low < res.memory_high_gb && res.memory_high_gb < max) {
            return Err(DaemonError::Validation(format!(
                "memory ordering must satisfy min < low < high < max ({} < {low} < {} < {max})",
                res.memory_floor_gb, res.memory_high_gb
            )));
        }
        if res.numa_nodes.is_empty() {
            return Err(DaemonError::Validation(
                "at least one NUMA node is required".to_string(),
            ));
        }
        for node in &res.numa_nodes {
            let path = format!("/sys/devices/system/node/node{node}");
            if !Path::new(&path).exists() {
                return Err(DaemonError::Validation(format!(
                    "NUMA node {node} does not exist"
                )));
            }
        }
        Ok(())
    }

    fn apply_to_cgroup(&self, config: &AgentConfig) -> Result<(), DaemonError> {
        let cgroup_path = self.cgroup_base.join(&config.agent.name);
        fs::create_dir_all(&cgroup_path)?;
        let res = &config.resources;
        for (name, gb) in [
            ("memory.min", res.memory_floor_gb),
            ("memory.low", res.effective_low()),
            ("memory.high", res.memory_high_gb),
            ("memory.max", res.effective_max()),
        ] {
            self.write_cgroup_file(
                &cgroup_path,
                name,
                &(gb * 1024 * 1024 * 1024).to_string(),
            )?;
        }
        self.write_cgroup_file(&cgroup_path, "memory.oom.group", "1")?;
        let mems = res
            .numa_nodes
            .iter()
            .map(u32::to_string)
            .collect::<Vec<_>>()
            .join(",");
        self.write_cgroup_file(&cgroup_path, "cpuset.mems", &mems)?;
        Ok(())
    }

    fn write_cgroup_file(&self, dir: &Path, name: &str, value: &str) -> Result<(), DaemonError> {
        let path = dir.join(name);
        fs::write(&path, value)?;
        let read_back = fs::read_to_string(&path)?;
        if read_back.trim() != value {
            return Err(DaemonError::Validation(format!(
                "write verification failed for {name}"
            )));
        }
        Ok(())
    }

    fn notify_residual_error(&self, error_message: &str) {
        error!("RESIDUAL notification: config error — {error_message}");
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt().with_target(false).with_level(true).init();
    let shutdown = Arc::new(AtomicBool::new(false));
    let shutdown_clone = Arc::clone(&shutdown);
    ctrlc::set_handler(move || shutdown_clone.store(true, Ordering::Relaxed))?;
    let mut daemon = CgroupDaemon::new()?;
    daemon.watch_config()?;
    daemon.run(shutdown)?;
    Ok(())
}
