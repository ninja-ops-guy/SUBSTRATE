//! Normalized event contracts for the agent/OS boundary.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum OsEvent {
    #[serde(rename = "process.crash")]
    ProcessCrash {
        pid: u32,
        uid: u32,
        comm: String,
        exe: String,
        signal: String,
        timestamp: String,
        source: String,
    },
}

impl OsEvent {
    pub fn source(&self) -> &str {
        match self {
            Self::ProcessCrash { source, .. } => source,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crash_event_is_structured_and_source_explicit() {
        let event = OsEvent::ProcessCrash {
            pid: 4312,
            uid: 1000,
            comm: "example".into(),
            exe: "/usr/bin/example".into(),
            signal: "SIGSEGV".into(),
            timestamp: "2026-09-24T14:32:10-04:00".into(),
            source: "systemd-coredump".into(),
        };
        let encoded = serde_json::to_value(&event).unwrap();
        assert_eq!(encoded["type"], "process.crash");
        assert_eq!(encoded["source"], "systemd-coredump");
    }
}
