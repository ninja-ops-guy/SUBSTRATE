//! KV-cache isolation boundary.

#[derive(Debug, Clone)]
pub struct KvIsolationConfig {
    pub app_cgroup: String,
    pub inference_cgroup: String,
    pub kv_cache_path: String,
    pub context_window: u32,
}

pub struct KvIsolation {
    config: KvIsolationConfig,
}

impl KvIsolation {
    pub fn new(config: KvIsolationConfig) -> Result<Self, String> {
        if config.app_cgroup == config.inference_cgroup {
            return Err("app and inference cgroups must be distinct".to_string());
        }
        if config.context_window == 0 {
            return Err("context_window must be non-zero".to_string());
        }
        Ok(Self { config })
    }

    pub fn config(&self) -> &KvIsolationConfig {
        &self.config
    }

    /// Records the boundary contract for rollback orchestration.
    ///
    /// Actual prefix reuse depends on the inference runtime and is not claimed
    /// by this method.
    pub fn validate_rollback_boundary(&self, last_verified_tokens: &[u32]) -> Result<(), String> {
        if last_verified_tokens.is_empty() {
            return Err("verified token history must not be empty".to_string());
        }
        Ok(())
    }
}
