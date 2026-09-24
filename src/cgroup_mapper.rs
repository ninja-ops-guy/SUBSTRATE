//! cgroup v2 hardware constraint mapper.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

#[derive(Debug, thiserror::Error)]
pub enum CgroupError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid configuration: {0}")]
    InvalidConfig(String),
    #[error("partial write detected: expected {expected}, wrote {actual}")]
    PartialWrite { expected: usize, actual: usize },
}

#[derive(Debug, Clone)]
pub struct CgroupConstraints {
    pub cgroup_name: String,
    pub memory_min_gb: u64,
    pub memory_low_gb: u64,
    pub memory_high_gb: u64,
    pub memory_max_gb: u64,
    pub numa_nodes: Vec<u32>,
    pub oom_group: bool,
}

pub struct CgroupMapper {
    base_path: PathBuf,
}

impl CgroupMapper {
    pub fn new() -> Result<Self, CgroupError> {
        Self::with_base("/sys/fs/cgroup")
    }

    pub fn with_base(base: impl Into<PathBuf>) -> Result<Self, CgroupError> {
        let base_path = base.into();
        if !base_path.exists() {
            return Err(CgroupError::InvalidConfig(format!(
                "cgroup v2 base does not exist: {}",
                base_path.display()
            )));
        }
        Ok(Self { base_path })
    }

    pub fn validate(constraints: &CgroupConstraints) -> Result<(), CgroupError> {
        if constraints.cgroup_name.is_empty()
            || constraints.cgroup_name.contains('/')
            || matches!(constraints.cgroup_name.as_str(), "." | "..")
        {
            return Err(CgroupError::InvalidConfig(
                "cgroup_name must be a safe single path component".to_string(),
            ));
        }
        if !(constraints.memory_min_gb < constraints.memory_low_gb
            && constraints.memory_low_gb < constraints.memory_high_gb
            && constraints.memory_high_gb < constraints.memory_max_gb)
        {
            return Err(CgroupError::InvalidConfig(
                "memory ordering must satisfy min < low < high < max".to_string(),
            ));
        }
        if constraints.numa_nodes.is_empty() {
            return Err(CgroupError::InvalidConfig(
                "at least one NUMA node is required".to_string(),
            ));
        }
        Ok(())
    }

    pub fn apply_constraints(&self, constraints: &CgroupConstraints) -> Result<(), CgroupError> {
        Self::validate(constraints)?;
        let cgroup_path = self.base_path.join(&constraints.cgroup_name);
        fs::create_dir_all(&cgroup_path)?;

        for (name, gb) in [
            ("memory.min", constraints.memory_min_gb),
            ("memory.low", constraints.memory_low_gb),
            ("memory.high", constraints.memory_high_gb),
            ("memory.max", constraints.memory_max_gb),
        ] {
            self.write_file(&cgroup_path, name, &(gb * 1024 * 1024 * 1024).to_string())?;
        }
        self.write_file(
            &cgroup_path,
            "memory.oom.group",
            if constraints.oom_group { "1" } else { "0" },
        )?;
        let mems = constraints
            .numa_nodes
            .iter()
            .map(u32::to_string)
            .collect::<Vec<_>>()
            .join(",");
        self.write_file(&cgroup_path, "cpuset.mems", &mems)?;
        self.verify_constraints(&cgroup_path, constraints)
    }

    fn write_file(&self, dir: &Path, name: &str, value: &str) -> Result<(), CgroupError> {
        let mut file = fs::File::create(dir.join(name))?;
        let actual = file.write(value.as_bytes())?;
        file.sync_all()?;
        if actual != value.len() {
            return Err(CgroupError::PartialWrite {
                expected: value.len(),
                actual,
            });
        }
        Ok(())
    }

    fn verify_constraints(&self, dir: &Path, c: &CgroupConstraints) -> Result<(), CgroupError> {
        for (name, expected) in [
            ("memory.min", c.memory_min_gb),
            ("memory.low", c.memory_low_gb),
            ("memory.high", c.memory_high_gb),
            ("memory.max", c.memory_max_gb),
        ] {
            let actual = fs::read_to_string(dir.join(name))?
                .trim()
                .parse::<u64>()
                .map_err(|_| CgroupError::InvalidConfig(format!("failed to parse {name}")))?;
            let expected = expected * 1024 * 1024 * 1024;
            if actual != expected {
                return Err(CgroupError::InvalidConfig(format!(
                    "{name} verification failed: expected {expected}, got {actual}"
                )));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid() -> CgroupConstraints {
        CgroupConstraints {
            cgroup_name: "agent-a".to_string(),
            memory_min_gb: 4,
            memory_low_gb: 8,
            memory_high_gb: 12,
            memory_max_gb: 16,
            numa_nodes: vec![0],
            oom_group: true,
        }
    }

    #[test]
    fn validates_memory_order() {
        assert!(CgroupMapper::validate(&valid()).is_ok());
        let mut invalid = valid();
        invalid.memory_low_gb = 4;
        assert!(CgroupMapper::validate(&invalid).is_err());
    }

    #[test]
    fn rejects_path_traversal_name() {
        let mut invalid = valid();
        invalid.cgroup_name = "../root".to_string();
        assert!(CgroupMapper::validate(&invalid).is_err());
    }
}
