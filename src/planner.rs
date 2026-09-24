//! Pre-flight capacity planner with GQA-aware KV-cache calculation.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelArchitecture {
    pub name: String,
    pub total_params_b: f64,
    pub num_layers: u32,
    pub num_query_heads: u32,
    pub num_kv_heads: u32,
    pub head_dim: u32,
    pub quant_format: String,
}

#[derive(Debug, Clone)]
pub struct MemoryRequirements {
    pub weights_gb: f64,
    pub kv_cache_gb: f64,
    pub scratchpad_gb: f64,
    pub total_gb: f64,
}

#[derive(Debug)]
pub enum SchedulingDecision {
    Accept {
        node: u32,
        requirements: MemoryRequirements,
    },
    DegradeToQuant {
        suggested: String,
        savings_gb: f64,
    },
    EvictLowerPriority {
        candidates: Vec<String>,
        freed_gb: f64,
    },
    QueueRequest {
        retry_after_secs: u64,
    },
    Reject {
        reason: String,
    },
}

#[derive(Debug, Clone)]
pub struct NodeMetrics {
    pub total_node_gb: u64,
    pub active_reservations_gb: u64,
    pub pinned_weights_gb: u64,
    pub kv_cache_gb: u64,
}

pub struct CapacityPlanner {
    topology: HashMap<u32, NodeMetrics>,
    model_registry: HashMap<String, ModelArchitecture>,
}

impl Default for CapacityPlanner {
    fn default() -> Self {
        Self::new()
    }
}

impl CapacityPlanner {
    pub fn new() -> Self {
        let mut model_registry = HashMap::new();
        for (tag, quant, params, layers, query_heads, kv_heads) in [
            ("llama3.3:70b-q4_k_m", "q4_k_m", 70.0, 80, 64, 8),
            ("llama3.3:70b-q3_k_m", "q3_k_m", 70.0, 80, 64, 8),
            ("qwen2.5-coder:32b-q4_k_m", "q4_k_m", 32.0, 64, 40, 8),
        ] {
            model_registry.insert(
                tag.to_string(),
                ModelArchitecture {
                    name: tag.to_string(),
                    total_params_b: params,
                    num_layers: layers,
                    num_query_heads: query_heads,
                    num_kv_heads: kv_heads,
                    head_dim: 128,
                    quant_format: quant.to_string(),
                },
            );
        }

        Self {
            topology: Self::detect_numa_topology(),
            model_registry,
        }
    }

    pub fn calculate_requirements(
        &self,
        model_name: &str,
        context_window: u32,
        precision_bytes: u32,
    ) -> Result<MemoryRequirements, String> {
        if context_window == 0 || precision_bytes == 0 {
            return Err("context_window and precision_bytes must be non-zero".to_string());
        }

        let arch = self
            .model_registry
            .get(model_name)
            .ok_or_else(|| format!("unknown model: {model_name}"))?;

        let bytes_per_param = match arch.quant_format.as_str() {
            "q3_k_m" => 0.42,
            "q4_k_m" => 0.55,
            "q5_k_m" => 0.68,
            "q8_0" => 1.0,
            _ => return Err(format!("unsupported quantization: {}", arch.quant_format)),
        };

        let weights_gb = arch.total_params_b * bytes_per_param;
        let kv_cache_gb = f64::from(context_window)
            * 2.0
            * f64::from(arch.num_kv_heads)
            * f64::from(arch.head_dim)
            * f64::from(precision_bytes)
            * f64::from(arch.num_layers)
            / (1024.0 * 1024.0 * 1024.0);
        let scratchpad_gb = 8.0;
        Ok(MemoryRequirements {
            weights_gb,
            kv_cache_gb,
            scratchpad_gb,
            total_gb: weights_gb + kv_cache_gb + scratchpad_gb,
        })
    }

    pub fn evaluate_admission(
        &self,
        model_name: &str,
        context_window: u32,
        preferred_node: Option<u32>,
    ) -> SchedulingDecision {
        let requirements = match self.calculate_requirements(model_name, context_window, 2) {
            Ok(req) => req,
            Err(reason) => return SchedulingDecision::Reject { reason },
        };
        let requested_gb = requirements.total_gb.ceil() as u64;

        if let Some(node) = preferred_node {
            if self.topology.get(&node).is_some_and(|m| {
                m.total_node_gb.saturating_sub(m.active_reservations_gb) >= requested_gb
            }) {
                return SchedulingDecision::Accept { node, requirements };
            }
        }

        if let Some(node) = self
            .topology
            .iter()
            .filter_map(|(id, metrics)| {
                let available = metrics
                    .total_node_gb
                    .saturating_sub(metrics.active_reservations_gb);
                (available >= requested_gb).then_some((*id, available))
            })
            .max_by_key(|(_, available)| *available)
            .map(|(id, _)| id)
        {
            return SchedulingDecision::Accept { node, requirements };
        }

        if model_name.ends_with("q4_k_m") {
            let suggested = model_name.replace("q4_k_m", "q3_k_m");
            if let Ok(degraded) = self.calculate_requirements(&suggested, context_window, 2) {
                return SchedulingDecision::DegradeToQuant {
                    savings_gb: requirements.total_gb - degraded.total_gb,
                    suggested,
                };
            }
        }

        SchedulingDecision::QueueRequest {
            retry_after_secs: 300,
        }
    }

    fn detect_numa_topology() -> HashMap<u32, NodeMetrics> {
        let mut topology = HashMap::new();
        for node_id in 0u32.. {
            let path = format!("/sys/devices/system/node/node{node_id}/meminfo");
            if !std::path::Path::new(&path).exists() {
                break;
            }
            let total_node_gb = std::fs::read_to_string(path)
                .ok()
                .and_then(|content| {
                    content
                        .lines()
                        .find(|line| line.contains("MemTotal:"))
                        .and_then(|line| line.split_whitespace().nth(2))
                        .and_then(|value| value.parse::<u64>().ok())
                })
                .map_or(0, |kb| kb / (1024 * 1024));
            topology.insert(
                node_id,
                NodeMetrics {
                    total_node_gb,
                    active_reservations_gb: 0,
                    pinned_weights_gb: 0,
                    kv_cache_gb: 0,
                },
            );
        }

        if topology.is_empty() {
            topology.insert(
                0,
                NodeMetrics {
                    total_node_gb: 128,
                    active_reservations_gb: 0,
                    pinned_weights_gb: 0,
                    kv_cache_gb: 0,
                },
            );
        }
        topology
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gqa_kv_cache_uses_kv_heads() {
        let planner = CapacityPlanner::new();
        let req = planner
            .calculate_requirements("llama3.3:70b-q4_k_m", 32_768, 2)
            .expect("requirements");
        assert!((req.kv_cache_gb - 10.0).abs() < 0.001);
        assert!((req.weights_gb - 38.5).abs() < 0.001);
    }

    #[test]
    fn rejects_zero_context() {
        let planner = CapacityPlanner::new();
        assert!(planner
            .calculate_requirements("llama3.3:70b-q4_k_m", 0, 2)
            .is_err());
    }
}
