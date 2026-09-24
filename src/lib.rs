//! OMARCHY-SRV library.
//!
//! RAM-first agentic server OS components for NUMA server hardware.

pub mod cgroup_mapper;
pub mod kv_isolation;
pub mod oom_matrix;
pub mod planner;
pub mod rollback;

pub use cgroup_mapper::{CgroupConstraints, CgroupMapper};
pub use planner::{CapacityPlanner, MemoryRequirements, SchedulingDecision};
pub use rollback::{RollbackCoordinator, UnifiedCheckpoint};
