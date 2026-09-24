//! Atomic configuration daemon.

use serde::{Deserialize, Serialize};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;

const SOCKET_PATH: &str = "/run/omarchy-srv/omarchy-configd.sock";
const STAGING_DIR: &str = "/run/omarchy-srv/staging";
const LIVE_DIR: &str = "/etc/omarchy-srv/live";
const RING_BUFFER_DIR: &str = "/etc/omarchy-srv/ring-buffer";
const MAX_PAYLOAD_BYTES: u64 = 1024 * 1024;

#[derive(Serialize, Deserialize, Clone, Debug)]
struct AgentConfig {
    agent: AgentMetadata,
    resources: ResourceAllocation,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct AgentMetadata {
    name: String,
    runtime: String,
    model: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct ResourceAllocation {
    memory_floor_gb: u64,
    memory_high_gb: u64,
    numa_policy: String,
    numa_nodes: Vec<u32>,
}

struct ConfigDaemon {
    listener: UnixListener,
}

impl ConfigDaemon {
    fn new() -> Result<Self, String> {
        fs::create_dir_all(STAGING_DIR).map_err(|e| format!("failed to create staging: {e}"))?;
        fs::create_dir_all(LIVE_DIR).map_err(|e| format!("failed to create live: {e}"))?;
        fs::create_dir_all(RING_BUFFER_DIR)
            .map_err(|e| format!("failed to create ring buffer: {e}"))?;

        if Path::new(SOCKET_PATH).exists() {
            fs::remove_file(SOCKET_PATH)
                .map_err(|e| format!("failed to remove stale socket: {e}"))?;
        }

        let listener =
            UnixListener::bind(SOCKET_PATH).map_err(|e| format!("failed to bind socket: {e}"))?;
        fs::set_permissions(SOCKET_PATH, fs::Permissions::from_mode(0o660))
            .map_err(|e| format!("failed to set socket permissions: {e}"))?;
        Ok(Self { listener })
    }

    fn run(&self) {
        println!("[omarchy-configd] listening on {SOCKET_PATH}");
        for stream in self.listener.incoming() {
            match stream {
                Ok(stream) => self.handle_client(stream),
                Err(e) => eprintln!("[omarchy-configd] connection error: {e}"),
            }
        }
    }

    fn handle_client(&self, mut stream: UnixStream) {
        let mut buffer = Vec::new();
        let read_result = {
            let mut limited = (&mut stream).take(MAX_PAYLOAD_BYTES + 1);
            limited.read_to_end(&mut buffer)
        };

        let response = match read_result {
            Ok(_) if buffer.len() as u64 > MAX_PAYLOAD_BYTES => {
                "ERROR: payload exceeds 1 MiB limit\n".to_string()
            }
            Ok(_) => match self.handle_transaction(&buffer) {
                Ok(()) => "SUCCESS: Config applied\n".to_string(),
                Err(e) => format!("ERROR: {e}\n"),
            },
            Err(e) => format!("ERROR: socket read failed: {e}\n"),
        };
        let _ = stream.write_all(response.as_bytes());
    }

    fn handle_transaction(&self, payload: &[u8]) -> Result<(), String> {
        let payload_text =
            std::str::from_utf8(payload).map_err(|e| format!("config is not valid UTF-8: {e}"))?;
        let config: AgentConfig =
            toml::from_str(payload_text).map_err(|e| format!("invalid TOML: {e}"))?;
        self.validate_static(&config)?;
        self.verify_capacity(&config)?;
        self.commit(payload)
    }

    fn validate_static(&self, config: &AgentConfig) -> Result<(), String> {
        if config.agent.name.trim().is_empty()
            || config.agent.runtime.trim().is_empty()
            || config.agent.model.trim().is_empty()
        {
            return Err("agent name, runtime, and model must be non-empty".to_string());
        }
        if config.resources.memory_floor_gb >= config.resources.memory_high_gb {
            return Err("memory_floor_gb must be less than memory_high_gb".to_string());
        }
        if config.resources.numa_nodes.is_empty() {
            return Err("at least one NUMA node is required".to_string());
        }
        if !matches!(
            config.resources.numa_policy.as_str(),
            "strict" | "strict_fail" | "preferred" | "interleave"
        ) {
            return Err(format!(
                "unsupported numa_policy: {}",
                config.resources.numa_policy
            ));
        }
        Ok(())
    }

    fn verify_capacity(&self, config: &AgentConfig) -> Result<(), String> {
        for node in &config.resources.numa_nodes {
            let meminfo = format!("/sys/devices/system/node/node{node}/meminfo");
            if !Path::new(&meminfo).exists() {
                return Err(format!("NUMA node {node} not found"));
            }
        }
        Ok(())
    }

    fn commit(&self, payload: &[u8]) -> Result<(), String> {
        let staging_file = format!("{STAGING_DIR}/agent.toml");
        fs::write(&staging_file, payload)
            .map_err(|e| format!("staging write failed: {e}"))?;

        let slot_0 = format!("{RING_BUFFER_DIR}/slot_0.toml");
        let slot_1 = format!("{RING_BUFFER_DIR}/slot_1.toml");
        let live_file = format!("{LIVE_DIR}/agent.toml");

        if Path::new(&slot_1).exists() {
            fs::remove_file(&slot_1)
                .map_err(|e| format!("old ring slot removal failed: {e}"))?;
        }
        if Path::new(&slot_0).exists() {
            fs::rename(&slot_0, &slot_1)
                .map_err(|e| format!("ring rotation failed: {e}"))?;
        }
        if Path::new(&live_file).exists() {
            fs::copy(&live_file, &slot_0).map_err(|e| format!("ring backup failed: {e}"))?;
        }

        let live_tmp = format!("{LIVE_DIR}/.agent.toml.new");
        fs::copy(&staging_file, &live_tmp)
            .map_err(|e| format!("live temp copy failed: {e}"))?;
        File::open(&live_tmp)
            .and_then(|file| file.sync_all())
            .map_err(|e| format!("live temp sync failed: {e}"))?;
        fs::rename(&live_tmp, &live_file)
            .map_err(|e| format!("atomic publish failed: {e}"))?;
        File::open(LIVE_DIR)
            .and_then(|dir| dir.sync_all())
            .map_err(|e| format!("live directory sync failed: {e}"))?;
        Ok(())
    }
}

fn main() {
    match ConfigDaemon::new() {
        Ok(daemon) => daemon.run(),
        Err(e) => {
            eprintln!("[omarchy-configd] failed to start: {e}");
            std::process::exit(1);
        }
    }
}
