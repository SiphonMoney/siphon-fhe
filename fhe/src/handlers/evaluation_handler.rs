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
    current_price_cents: u32,
    encrypted_client_key: String, // Client key for decryption
}

#[derive(Serialize)]
pub struct EvaluationResponse {
    is_triggered: bool,
}

/// Decrypt the FHE result using the client key
fn decrypt_result(encrypted_result: &RadixCiphertext, client_key_hex: &str) -> bool {
    println!("[FHE Engine] Decrypting result...");
    let client_key_bytes = hex::decode(client_key_hex).unwrap();
    let client_key: RadixClientKey = bincode::deserialize(&client_key_bytes).unwrap();
    client_key.decrypt::<u64>(encrypted_result) == 1
}

pub async fn evaluate_strategy(
    Json(payload): Json<EvaluationPayload>,
) -> (StatusCode, Json<EvaluationResponse>) {
    
    println!("[Rust FHE Engine] Received evaluation request.");

    // 1. Deserialize the server key
    let server_key: ServerKey = bincode::deserialize(&hex::decode(payload.server_key).unwrap()).unwrap();
    
    // 2. Perform homomorphic computation based on strategy type
    let encrypted_result = match payload.strategy_type.as_str() {
        "LIMIT_ORDER" | "BRACKET_ORDER_SHORT" => {
            let enc_upper: RadixCiphertext = bincode::deserialize(&hex::decode(payload.encrypted_upper_bound).unwrap()).unwrap();
            let enc_lower: RadixCiphertext = bincode::deserialize(&hex::decode(payload.encrypted_lower_bound).unwrap()).unwrap();
            
            let is_above = fhe_core::homomorphic_check(&server_key, &enc_upper, "GTE", payload.current_price_cents);
            let is_below = fhe_core::homomorphic_check(&server_key, &enc_lower, "LTE", payload.current_price_cents);

            fhe_core::homomorphic_or(&server_key, &is_above, &is_below)
        },
        "LIMIT_BUY_DIP" => {
             let enc_lower: RadixCiphertext = bincode::deserialize(&hex::decode(payload.encrypted_lower_bound).unwrap()).unwrap();
             fhe_core::homomorphic_check(&server_key, &enc_lower, "LTE", payload.current_price_cents)
        },
        "LIMIT_SELL_RALLY" => {
             let enc_upper: RadixCiphertext = bincode::deserialize(&hex::decode(payload.encrypted_upper_bound).unwrap()).unwrap();
             fhe_core::homomorphic_check(&server_key, &enc_upper, "GTE", payload.current_price_cents)
        },
        _ => {
            println!("[Rust FHE Engine] ❌ Error: Unknown strategy type '{}'", payload.strategy_type);
            return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { is_triggered: false }));
        }
    };
    
    // 3. Decrypt the result using the client key
    let is_triggered = decrypt_result(&encrypted_result, &payload.encrypted_client_key);

    println!("[Rust FHE Engine] Evaluation complete. is_triggered: {}", is_triggered);
    (StatusCode::OK, Json(EvaluationResponse { is_triggered }))
}