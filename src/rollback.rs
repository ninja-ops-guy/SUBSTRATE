//! Unified checkpoint coordinator.

use reqwest::blocking::Client;
use serde_json::json;
use std::process::Command;
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct UnifiedCheckpoint {
    pub checkpoint_id: String,
    pub git_sha: String,
    pub btrfs_snapshot_id: String,
    /// Ollama currently exposes model unload, not per-session deletion.
    pub ollama_model: String,
}

#[derive(Debug, thiserror::Error)]
pub enum RollbackError {
    #[error("freeze failed: {0}")]
    Freeze(String),
    #[error("Ollama unload failed: {0}")]
    OllamaUnload(String),
    #[error("filesystem rollback failed: {0}")]
    Filesystem(String),
    #[error("git reset failed: {0}")]
    Git(String),
}

pub struct RollbackCoordinator {
    pub cgroup_path: String,
    pub workspace_path: String,
    pub snapshot_path: String,
    pub ollama_endpoint: String,
}

impl RollbackCoordinator {
    pub fn execute_rollback(&self, cp: &UnifiedCheckpoint) -> Result<(), RollbackError> {
        self.set_frozen(true)?;

        if let Err(error) = self.unload_ollama_model(&cp.ollama_model) {
            let _ = self.set_frozen(false);
            return Err(RollbackError::OllamaUnload(error));
        }

        if let Err(error) = self.rollback_filesystem(&cp.btrfs_snapshot_id) {
            let _ = self.set_frozen(false);
            return Err(error);
        }

        if let Err(error) = self.reset_git(&cp.git_sha) {
            let _ = self.set_frozen(false);
            return Err(error);
        }

        self.set_frozen(false)
    }

    fn set_frozen(&self, frozen: bool) -> Result<(), RollbackError> {
        std::fs::write(
            format!("{}/cgroup.freeze", self.cgroup_path),
            if frozen { "1" } else { "0" },
        )
        .map_err(|e| RollbackError::Freeze(e.to_string()))
    }

    fn unload_ollama_model(&self, model: &str) -> Result<(), String> {
        if model.trim().is_empty() {
            return Err("checkpoint has no Ollama model".to_string());
        }
        let url = format!(
            "{}/api/generate",
            self.ollama_endpoint.trim_end_matches('/')
        );
        let response = Client::new()
            .post(url)
            .timeout(Duration::from_secs(10))
            .json(&json!({
                "model": model,
                "prompt": "",
                "stream": false,
                "keep_alive": 0
            }))
            .send()
            .map_err(|e| e.to_string())?;

        if !response.status().is_success() {
            return Err(format!("Ollama returned {}", response.status()));
        }
        Ok(())
    }

    fn rollback_filesystem(&self, snapshot_id: &str) -> Result<(), RollbackError> {
        if snapshot_id.contains('/') || snapshot_id == "." || snapshot_id == ".." {
            return Err(RollbackError::Filesystem(
                "invalid snapshot identifier".to_string(),
            ));
        }
        let delete = Command::new("btrfs")
            .args(["subvolume", "delete", &self.workspace_path])
            .status()
            .map_err(|e| RollbackError::Filesystem(e.to_string()))?;
        if !delete.success() {
            return Err(RollbackError::Filesystem("btrfs delete failed".to_string()));
        }

        let snapshot = format!("{}/{}", self.snapshot_path, snapshot_id);
        let restore = Command::new("btrfs")
            .args(["subvolume", "snapshot", &snapshot, &self.workspace_path])
            .status()
            .map_err(|e| RollbackError::Filesystem(e.to_string()))?;
        if !restore.success() {
            return Err(RollbackError::Filesystem(
                "btrfs snapshot restore failed".to_string(),
            ));
        }
        Ok(())
    }

    fn reset_git(&self, git_sha: &str) -> Result<(), RollbackError> {
        if git_sha.is_empty() || !git_sha.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(RollbackError::Git("invalid git SHA".to_string()));
        }
        let status = Command::new("git")
            .args(["-C", &self.workspace_path, "reset", "--hard", git_sha])
            .status()
            .map_err(|e| RollbackError::Git(e.to_string()))?;
        if status.success() {
            Ok(())
        } else {
            Err(RollbackError::Git("git reset failed".to_string()))
        }
    }
}
