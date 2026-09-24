//! OMARCHY-SRV library.
//!
//! RAM-first agentic server OS components for NUMA server hardware.

pub mod audit;
pub mod cgroup_mapper;
pub mod events;
pub mod kv_isolation;
pub mod oom_matrix;
pub mod os_boundary;
pub mod planner;
pub mod rollback;

pub use cgroup_mapper::{CgroupConstraints, CgroupMapper};
pub use planner::{CapacityPlanner, MemoryRequirements, SchedulingDecision};
pub use rollback::{RollbackCoordinator, UnifiedCheckpoint};
