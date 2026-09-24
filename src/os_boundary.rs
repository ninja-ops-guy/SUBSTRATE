//! Typed semantic OS boundary.
//!
//! Models and harnesses may request operations. They do not authorize them.
//! This module is deliberately policy-only: privileged execution belongs in
//! a separately qualified broker/helper.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum PermissionClass {
    Read,
    Change,
    Dangerous,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OperationRequest {
    pub task_id: String,
    pub operation: String,
    #[serde(default)]
    pub arguments: BTreeMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClassifiedRequest {
    pub request: OperationRequest,
    pub permission_class: PermissionClass,
    pub requires_privilege: bool,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum BoundaryError {
    #[error("unknown OS operation: {0}")]
    UnknownOperation(String),
    #[error("task_id must not be empty")]
    MissingTaskId,
}

pub fn classify(request: OperationRequest) -> Result<ClassifiedRequest, BoundaryError> {
    if request.task_id.trim().is_empty() {
        return Err(BoundaryError::MissingTaskId);
    }

    let (permission_class, requires_privilege) = match request.operation.as_str() {
        "system.identify"
        | "memory.snapshot"
        | "crash.list"
        | "crash.inspect"
        | "journal.query"
        | "service.status"
        | "package.query"
        | "network.snapshot"
        | "cgroup.usage"
        | "governor.metrics"
        | "snapshot.list" => (PermissionClass::Read, false),

        "service.restart"
        | "package.install"
        | "config.patch"
        | "snapshot.create" => (PermissionClass::Change, true),

        "snapshot.rollback"
        | "cgroup.set_limit"
        | "governor.set_policy"
        | "system.boot_config"
        | "storage.partition"
        | "security.policy_change" => (PermissionClass::Dangerous, true),

        other => return Err(BoundaryError::UnknownOperation(other.to_owned())),
    };

    Ok(ClassifiedRequest {
        request,
        permission_class,
        requires_privilege,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(operation: &str) -> OperationRequest {
        OperationRequest {
            task_id: "task-1".into(),
            operation: operation.into(),
            arguments: BTreeMap::new(),
        }
    }

    #[test]
    fn read_operations_are_read_only() {
        let classified = classify(request("crash.inspect")).unwrap();
        assert_eq!(classified.permission_class, PermissionClass::Read);
        assert!(!classified.requires_privilege);
    }

    #[test]
    fn mutations_do_not_collapse_into_read() {
        let classified = classify(request("service.restart")).unwrap();
        assert_eq!(classified.permission_class, PermissionClass::Change);
        assert!(classified.requires_privilege);
    }

    #[test]
    fn high_risk_operations_are_dangerous() {
        let classified = classify(request("snapshot.rollback")).unwrap();
        assert_eq!(classified.permission_class, PermissionClass::Dangerous);
        assert!(classified.requires_privilege);
    }

    #[test]
    fn unknown_operations_fail_closed() {
        assert_eq!(
            classify(request("shell.exec")),
            Err(BoundaryError::UnknownOperation("shell.exec".into()))
        );
    }
}
