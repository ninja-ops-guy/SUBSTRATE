//! Typed semantic OS boundary.
//!
//! Classification is not authorization. The broker validates caller identity,
//! server-issued task scope, arguments and approvals before any dispatch.
//! Rust and the broker share the same version-controlled operation catalog.

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
#[serde(deny_unknown_fields)]
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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OperationMetadata {
    permission_class: PermissionClass,
    requires_privilege: bool,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum BoundaryError {
    #[error("unknown OS operation: {0}")]
    UnknownOperation(String),
    #[error("task_id must not be empty")]
    MissingTaskId,
    #[error("invalid embedded operation catalog")]
    InvalidCatalog,
}

pub fn classify(request: OperationRequest) -> Result<ClassifiedRequest, BoundaryError> {
    if request.task_id.trim().is_empty() {
        return Err(BoundaryError::MissingTaskId);
    }
    let catalog: BTreeMap<String, OperationMetadata> =
        serde_json::from_str(include_str!("os_operations.json"))
            .map_err(|_| BoundaryError::InvalidCatalog)?;
    let metadata = catalog
        .get(&request.operation)
        .ok_or_else(|| BoundaryError::UnknownOperation(request.operation.clone()))?;
    Ok(ClassifiedRequest {
        request,
        permission_class: metadata.permission_class,
        requires_privilege: metadata.requires_privilege,
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

    #[test]
    fn catalog_contains_all_contract_operations() {
        let catalog: BTreeMap<String, OperationMetadata> =
            serde_json::from_str(include_str!("os_operations.json")).unwrap();
        assert_eq!(catalog.len(), 21);
        for operation in catalog.keys() {
            assert!(classify(request(operation)).is_ok());
        }
    }

    #[test]
    fn request_cannot_supply_its_own_permission_class() {
        let forged =
            r#"{"task_id":"task-1","operation":"service.restart","permission_class":"READ"}"#;
        assert!(serde_json::from_str::<OperationRequest>(forged).is_err());
    }
}
