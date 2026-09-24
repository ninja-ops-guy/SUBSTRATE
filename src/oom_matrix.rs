//! OOM protection matrix.

use std::fs;
use std::path::Path;

#[derive(Debug, Clone)]
pub struct OomTier {
    pub name: String,
    pub oom_score_adj: i32,
    pub memory_min_gb: Option<u64>,
}

pub struct OomMatrix {
    tiers: Vec<OomTier>,
}

impl Default for OomMatrix {
    fn default() -> Self {
        Self::new()
    }
}

impl OomMatrix {
    pub fn new() -> Self {
        Self {
            tiers: vec![
                OomTier {
                    name: "protected".to_string(),
                    oom_score_adj: -1000,
                    memory_min_gb: Some(4),
                },
                OomTier {
                    name: "standard".to_string(),
                    oom_score_adj: 0,
                    memory_min_gb: None,
                },
            ],
        }
    }

    pub fn apply_protection(&self, pid: u32, tier: &str) -> Result<(), String> {
        let tier = self
            .tiers
            .iter()
            .find(|candidate| candidate.name == tier)
            .ok_or_else(|| format!("unknown tier: {tier}"))?;
        fs::write(
            format!("/proc/{pid}/oom_score_adj"),
            tier.oom_score_adj.to_string(),
        )
        .map_err(|e| format!("failed to set oom_score_adj: {e}"))
    }

    pub fn setup_system_cgroup(&self) -> Result<(), String> {
        let system_cgroup = Path::new("/sys/fs/cgroup/omarchy-system");
        fs::create_dir_all(system_cgroup)
            .map_err(|e| format!("failed to create system cgroup: {e}"))?;
        fs::write(
            system_cgroup.join("memory.min"),
            (4_u64 * 1024 * 1024 * 1024).to_string(),
        )
        .map_err(|e| format!("failed to set memory.min: {e}"))
    }
}
