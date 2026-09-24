//! Structured audit records for semantic OS operations.

use crate::os_boundary::PermissionClass;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PolicyDecision {
    Allowed,
    Approved,
    Denied,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OperationAuditRecord {
    pub task_id: String,
    pub correlation_id: String,
    pub actor: String,
    pub operation: String,
    pub arguments: BTreeMap<String, String>,
    pub permission_class: PermissionClass,
    pub decision: PolicyDecision,
    pub approved_by: Option<String>,
    pub exit_code: Option<i32>,
}
