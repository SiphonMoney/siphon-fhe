use axum::{http::StatusCode, Json};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use tfhe::integer::{CompressedServerKey, RadixCiphertext, ServerKey};
use crate::fhe_engine::core as fhe_core;

// ── Wire format ────────────────────────────────────────────────────────────────
// The engine no longer holds the client key and no longer decrypts. It returns the
// *encrypted* trigger result (hex of a RadixCiphertext encoding 1=triggered / 0=not).
// The browser decrypts it locally with its client key, which never leaves the device.

#[derive(Deserialize)]
pub struct EvaluationPayload {
    strategy_type: String,
    encrypted_upper_bound: String,
    encrypted_lower_bound: String,
    server_key: String,
    current_price_cents: u64,  // u64 — u32 overflows at ~$42k (BTC/ETH prices)
}

#[derive(Serialize)]
pub struct EvaluationResponse {
    /// Hex of the result RadixCiphertext (1=triggered, 0=not). None on error.
    #[serde(skip_serializing_if = "Option::is_none")]
    encrypted_result: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

impl EvaluationResponse {
    fn ok(encrypted_result: String) -> Self {
        Self { encrypted_result: Some(encrypted_result), error: None }
    }
    fn err(msg: String) -> Self {
        Self { encrypted_result: None, error: Some(msg) }
    }
}

fn deserialize_hex<T: for<'de> serde::Deserialize<'de>>(hex_str: &str, label: &str) -> Result<T, String> {
    let bytes = hex::decode(hex_str)
        .map_err(|e| format!("hex decode failed for {}: {}", label, e))?;
    bincode::deserialize(&bytes)
        .map_err(|e| format!("bincode deserialize failed for {}: {}", label, e))
}

fn serialize_hex(ct: &RadixCiphertext) -> Result<String, String> {
    bincode::serialize(ct)
        .map(hex::encode)
        .map_err(|e| format!("bincode serialize failed for result: {}", e))
}

/// Deserialize the user's server key. The browser now sends a *compressed* server key
/// (~20MB hex vs ~200MB expanded), so we deserialize that and decompress. Older clients
/// (legacy payload-generator) sent an already-expanded `ServerKey`; we fall back to that
/// format so both wire formats keep working.
fn deserialize_server_key(hex_str: &str) -> Result<ServerKey, String> {
    let bytes = hex::decode(hex_str)
        .map_err(|e| format!("hex decode failed for server_key: {}", e))?;
    match bincode::deserialize::<CompressedServerKey>(&bytes) {
        Ok(compressed) => Ok(ServerKey::from(compressed)),
        Err(compressed_err) => bincode::deserialize::<ServerKey>(&bytes).map_err(|expanded_err| {
            format!(
                "server_key deserialize failed (compressed: {}; expanded: {})",
                compressed_err, expanded_err
            )
        }),
    }
}

/// Convenience: build an error tuple.
fn bad(code: StatusCode, msg: String) -> (StatusCode, Json<EvaluationResponse>) {
    (code, Json(EvaluationResponse::err(msg)))
}

pub async fn evaluate_strategy(
    Json(payload): Json<EvaluationPayload>,
) -> (StatusCode, Json<EvaluationResponse>) {
    println!("[FHE Engine] Evaluating strategy_type='{}' current_price_cents={}",
        payload.strategy_type, payload.current_price_cents);

    let server_key: ServerKey = match deserialize_server_key(&payload.server_key) {
        Ok(k) => k,
        Err(e) => return bad(StatusCode::BAD_REQUEST, e),
    };

    let price = payload.current_price_cents;

    let encrypted_result = match payload.strategy_type.as_str() {
        "LIMIT_ORDER" | "BRACKET_ORDER_SHORT" => {
            let enc_upper: RadixCiphertext = match deserialize_hex(&payload.encrypted_upper_bound, "upper_bound") {
                Ok(v) => v, Err(e) => return bad(StatusCode::BAD_REQUEST, e),
            };
            let enc_lower: RadixCiphertext = match deserialize_hex(&payload.encrypted_lower_bound, "lower_bound") {
                Ok(v) => v, Err(e) => return bad(StatusCode::BAD_REQUEST, e),
            };
            let is_above = match fhe_core::homomorphic_check(&server_key, &enc_upper, "GTE", price) {
                Ok(v) => v, Err(e) => return bad(StatusCode::INTERNAL_SERVER_ERROR, e),
            };
            let is_below = match fhe_core::homomorphic_check(&server_key, &enc_lower, "LTE", price) {
                Ok(v) => v, Err(e) => return bad(StatusCode::INTERNAL_SERVER_ERROR, e),
            };
            fhe_core::homomorphic_or(&server_key, &is_above, &is_below)
        },
        "LIMIT_BUY_DIP" => {
            let enc_lower: RadixCiphertext = match deserialize_hex(&payload.encrypted_lower_bound, "lower_bound") {
                Ok(v) => v, Err(e) => return bad(StatusCode::BAD_REQUEST, e),
            };
            match fhe_core::homomorphic_check(&server_key, &enc_lower, "LTE", price) {
                Ok(v) => v, Err(e) => return bad(StatusCode::INTERNAL_SERVER_ERROR, e),
            }
        },
        "LIMIT_SELL_RALLY" => {
            let enc_upper: RadixCiphertext = match deserialize_hex(&payload.encrypted_upper_bound, "upper_bound") {
                Ok(v) => v, Err(e) => return bad(StatusCode::BAD_REQUEST, e),
            };
            match fhe_core::homomorphic_check(&server_key, &enc_upper, "GTE", price) {
                Ok(v) => v, Err(e) => return bad(StatusCode::INTERNAL_SERVER_ERROR, e),
            }
        },
        _ => {
            let msg = format!("Unknown strategy_type '{}'", payload.strategy_type);
            println!("[FHE Engine] ❌ {}", msg);
            return bad(StatusCode::BAD_REQUEST, msg);
        }
    };

    match serialize_hex(&encrypted_result) {
        Ok(hex) => {
            println!("[FHE Engine] ✅ Evaluation complete, returning encrypted result ({} hex chars)", hex.len());
            (StatusCode::OK, Json(EvaluationResponse::ok(hex)))
        },
        Err(e) => bad(StatusCode::INTERNAL_SERVER_ERROR, e),
    }
}

#[derive(Deserialize)]
pub struct ConditionPayload {
    encrypted_bound: String,
    condition: String,          // "GTE" or "LTE"
    current_price_cents: u64,
    server_key: String,
}

pub async fn evaluate_condition(
    Json(payload): Json<ConditionPayload>,
) -> (StatusCode, Json<EvaluationResponse>) {
    println!("[FHE Engine] evaluateCondition condition='{}' price_cents={}",
        payload.condition, payload.current_price_cents);

    let server_key: ServerKey = match deserialize_server_key(&payload.server_key) {
        Ok(k) => k,
        Err(e) => return bad(StatusCode::BAD_REQUEST, e),
    };

    let enc_bound: RadixCiphertext = match deserialize_hex(&payload.encrypted_bound, "encrypted_bound") {
        Ok(v) => v,
        Err(e) => return bad(StatusCode::BAD_REQUEST, e),
    };

    let result = match fhe_core::homomorphic_check(
        &server_key, &enc_bound, &payload.condition, payload.current_price_cents
    ) {
        Ok(v) => v,
        Err(e) => return bad(StatusCode::INTERNAL_SERVER_ERROR, e),
    };

    match serialize_hex(&result) {
        Ok(hex) => (StatusCode::OK, Json(EvaluationResponse::ok(hex))),
        Err(e) => bad(StatusCode::INTERNAL_SERVER_ERROR, e),
    }
}

// ── Condition trees ─────────────────────────────────────────────────────────────
// Boolean composition (AND/OR/NOT) used to happen in Python over plaintext booleans.
// Now that results are encrypted, the whole tree must be folded homomorphically here.

#[derive(Deserialize)]
pub struct TreePayload {
    tree: Value,
    server_key: String,
    /// price_feed_id -> current price in cents
    prices: HashMap<String, u64>,
}

fn eval_tree_node(
    sks: &ServerKey,
    node: &Value,
    prices: &HashMap<String, u64>,
) -> Result<RadixCiphertext, String> {
    let op = node.get("op").and_then(|v| v.as_str())
        .ok_or_else(|| "tree node missing 'op'".to_string())?;

    match op {
        "LEAF" => {
            let bound_hex = node.get("encrypted_bound").and_then(|v| v.as_str())
                .ok_or_else(|| "LEAF missing 'encrypted_bound'".to_string())?;
            let condition = node.get("condition").and_then(|v| v.as_str())
                .ok_or_else(|| "LEAF missing 'condition'".to_string())?;
            let feed = node.get("price_feed_id").and_then(|v| v.as_str())
                .ok_or_else(|| "LEAF missing 'price_feed_id'".to_string())?;
            let price = *prices.get(feed)
                .ok_or_else(|| format!("no price for feed '{}'", feed))?;
            let enc_bound: RadixCiphertext = deserialize_hex(bound_hex, "encrypted_bound")?;
            fhe_core::homomorphic_check(sks, &enc_bound, condition, price)
        }
        "AND" | "OR" => {
            let children = node.get("conditions").and_then(|v| v.as_array())
                .filter(|c| !c.is_empty())
                .ok_or_else(|| format!("{} node has no 'conditions'", op))?;
            let mut acc = eval_tree_node(sks, &children[0], prices)?;
            for child in &children[1..] {
                let next = eval_tree_node(sks, child, prices)?;
                acc = if op == "AND" {
                    fhe_core::homomorphic_and(sks, &acc, &next)
                } else {
                    fhe_core::homomorphic_or(sks, &acc, &next)
                };
            }
            Ok(acc)
        }
        "NOT" => {
            let children = node.get("conditions").and_then(|v| v.as_array())
                .filter(|c| !c.is_empty())
                .ok_or_else(|| "NOT node has no 'conditions'".to_string())?;
            let inner = eval_tree_node(sks, &children[0], prices)?;
            Ok(fhe_core::homomorphic_not(sks, &inner))
        }
        other => Err(format!("unknown tree op '{}'", other)),
    }
}

pub async fn evaluate_tree(
    Json(payload): Json<TreePayload>,
) -> (StatusCode, Json<EvaluationResponse>) {
    println!("[FHE Engine] evaluateTree with {} price feeds", payload.prices.len());

    let server_key: ServerKey = match deserialize_server_key(&payload.server_key) {
        Ok(k) => k,
        Err(e) => return bad(StatusCode::BAD_REQUEST, e),
    };

    let result = match eval_tree_node(&server_key, &payload.tree, &payload.prices) {
        Ok(v) => v,
        Err(e) => return bad(StatusCode::BAD_REQUEST, e),
    };

    match serialize_hex(&result) {
        Ok(hex) => (StatusCode::OK, Json(EvaluationResponse::ok(hex))),
        Err(e) => bad(StatusCode::INTERNAL_SERVER_ERROR, e),
    }
}
