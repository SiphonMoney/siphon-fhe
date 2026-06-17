use axum::{http::StatusCode, Json};
use serde::{Deserialize, Serialize};
use tfhe::integer::{RadixClientKey, RadixCiphertext, ServerKey};
use crate::fhe_engine::core as fhe_core;

#[derive(Deserialize)]
pub struct EvaluationPayload {
    strategy_type: String,
    encrypted_upper_bound: String,
    encrypted_lower_bound: String,
    server_key: String,
    current_price_cents: u64,  // u64 — u32 overflows at ~$42k (BTC/ETH prices)
    encrypted_client_key: String,
}

#[derive(Serialize)]
pub struct EvaluationResponse {
    is_triggered: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

fn deserialize_hex<T: for<'de> serde::Deserialize<'de>>(hex_str: &str, label: &str) -> Result<T, String> {
    let bytes = hex::decode(hex_str)
        .map_err(|e| format!("hex decode failed for {}: {}", label, e))?;
    bincode::deserialize(&bytes)
        .map_err(|e| format!("bincode deserialize failed for {}: {}", label, e))
}

fn decrypt_result(encrypted_result: &RadixCiphertext, client_key_hex: &str) -> Result<bool, String> {
    let client_key: RadixClientKey = deserialize_hex(client_key_hex, "client_key")?;
    Ok(client_key.decrypt::<u64>(encrypted_result) == 1)
}

pub async fn evaluate_strategy(
    Json(payload): Json<EvaluationPayload>,
) -> (StatusCode, Json<EvaluationResponse>) {
    println!("[FHE Engine] Evaluating strategy_type='{}' current_price_cents={}",
        payload.strategy_type, payload.current_price_cents);

    let server_key: ServerKey = match deserialize_hex(&payload.server_key, "server_key") {
        Ok(k) => k,
        Err(e) => {
            println!("[FHE Engine] ❌ {}", e);
            return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { is_triggered: false, error: Some(e) }));
        }
    };

    let price = payload.current_price_cents;

    let encrypted_result = match payload.strategy_type.as_str() {
        "LIMIT_ORDER" | "BRACKET_ORDER_SHORT" => {
            let enc_upper: RadixCiphertext = match deserialize_hex(&payload.encrypted_upper_bound, "upper_bound") {
                Ok(v) => v, Err(e) => return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
            };
            let enc_lower: RadixCiphertext = match deserialize_hex(&payload.encrypted_lower_bound, "lower_bound") {
                Ok(v) => v, Err(e) => return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
            };
            let is_above = match fhe_core::homomorphic_check(&server_key, &enc_upper, "GTE", price) {
                Ok(v) => v, Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
            };
            let is_below = match fhe_core::homomorphic_check(&server_key, &enc_lower, "LTE", price) {
                Ok(v) => v, Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
            };
            fhe_core::homomorphic_or(&server_key, &is_above, &is_below)
        },
        "LIMIT_BUY_DIP" => {
            let enc_lower: RadixCiphertext = match deserialize_hex(&payload.encrypted_lower_bound, "lower_bound") {
                Ok(v) => v, Err(e) => return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
            };
            match fhe_core::homomorphic_check(&server_key, &enc_lower, "LTE", price) {
                Ok(v) => v, Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
            }
        },
        "LIMIT_SELL_RALLY" => {
            let enc_upper: RadixCiphertext = match deserialize_hex(&payload.encrypted_upper_bound, "upper_bound") {
                Ok(v) => v, Err(e) => return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
            };
            match fhe_core::homomorphic_check(&server_key, &enc_upper, "GTE", price) {
                Ok(v) => v, Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
            }
        },
        _ => {
            let msg = format!("Unknown strategy_type '{}'", payload.strategy_type);
            println!("[FHE Engine] ❌ {}", msg);
            return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { is_triggered: false, error: Some(msg) }));
        }
    };

    match decrypt_result(&encrypted_result, &payload.encrypted_client_key) {
        Ok(is_triggered) => {
            println!("[FHE Engine] ✅ Evaluation complete. is_triggered={}", is_triggered);
            (StatusCode::OK, Json(EvaluationResponse { is_triggered, error: None }))
        },
        Err(e) => {
            println!("[FHE Engine] ❌ Decrypt failed: {}", e);
            (StatusCode::INTERNAL_SERVER_ERROR, Json(EvaluationResponse { is_triggered: false, error: Some(e) }))
        }
    }
}

#[derive(Deserialize)]
pub struct ConditionPayload {
    encrypted_bound: String,
    condition: String,          // "GTE" or "LTE"
    current_price_cents: u64,
    server_key: String,
    encrypted_client_key: String,
}

pub async fn evaluate_condition(
    Json(payload): Json<ConditionPayload>,
) -> (StatusCode, Json<EvaluationResponse>) {
    println!("[FHE Engine] evaluateCondition condition='{}' price_cents={}",
        payload.condition, payload.current_price_cents);

    let server_key: ServerKey = match deserialize_hex(&payload.server_key, "server_key") {
        Ok(k) => k,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
    };

    let enc_bound: RadixCiphertext = match deserialize_hex(&payload.encrypted_bound, "encrypted_bound") {
        Ok(v) => v,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
    };

    let result = match fhe_core::homomorphic_check(
        &server_key, &enc_bound, &payload.condition, payload.current_price_cents
    ) {
        Ok(v) => v,
        Err(e) => return (StatusCode::INTERNAL_SERVER_ERROR, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
    };

    match decrypt_result(&result, &payload.encrypted_client_key) {
        Ok(is_triggered) => {
            println!("[FHE Engine] ✅ evaluateCondition result: {}", is_triggered);
            (StatusCode::OK, Json(EvaluationResponse { is_triggered, error: None }))
        },
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(EvaluationResponse { is_triggered: false, error: Some(e) })),
    }
}
