use axum::{http::StatusCode, Json};
use serde::{Deserialize, Serialize};
use tfhe::integer::{RadixClientKey, RadixCiphertext, ServerKey};
use crate::fhe_engine::core as fhe_core;
use crate::mpc_client::MPCClient;


#[derive(Deserialize)]
pub struct EvaluationPayload {
    strategy_type: String,
    encrypted_upper_bound: String,
    encrypted_lower_bound: String,
    server_key: String,
    current_price_cents: u32,
    encrypted_client_key: Option<String>, // Optional - only if shares NOT stored on MPC
    mpc_public_key_set: Option<String>, // MPC public key set for distributed key
    mpc_share_indices: Option<Vec<usize>>, // Which MPC servers hold shares
    fhe_key_id: Option<String>, // Key ID when shares are stored on MPC servers
}

#[derive(Serialize)]
pub struct EvaluationResponse {
    is_triggered: bool,
}

/// This function simulates a Trusted Execution Environment (TEE).
fn simulate_tee_decryption(encrypted_result: &RadixCiphertext, client_key_hex: &str) -> bool {
    println!("[TEE Simulation] Performing secure decryption inside the enclave...");
    // The TEE can safely deserialize the client key to use it for the one-time decryption.
    let client_key_bytes = hex::decode(client_key_hex).unwrap();
    let client_key: RadixClientKey = bincode::deserialize(&client_key_bytes).unwrap();
    client_key.decrypt::<u64>(encrypted_result) == 1
}


pub async fn evaluate_strategy(
    Json(payload): Json<EvaluationPayload>,
) -> (StatusCode, Json<EvaluationResponse>) {
    
    println!("[Rust FHE Engine] Received REAL evaluation request from Python orchestrator.");

    // 1. Deserialize the raw byte data from the JSON payload into real FHE objects.
    let server_key: ServerKey = bincode::deserialize(&hex::decode(payload.server_key).unwrap()).unwrap();
    
    // 2. Perform the real homomorphic computation based on the strategy type.
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
    
    // 3. Decrypt the result - ALWAYS use MPC if key_id is provided (no direct decryption)
    let is_triggered = if let Some(key_id) = &payload.fhe_key_id {
        println!("[Rust FHE Engine] Using MPC threshold decryption (key_id: {})...", key_id);
        
        // Serialize the encrypted result for MPC servers
        let encrypted_result_hex = hex::encode(bincode::serialize(&encrypted_result).unwrap());
        
        // Create MPC client and request threshold decryption
        let mpc_client = MPCClient::new();
        
        // Check if MPC servers are available
        let mpc_available = mpc_client.check_health().await;
        
        if mpc_available {
            println!("[Rust FHE Engine] MPC servers available, requesting threshold decryption...");
            
            // Request threshold decryption using key shares stored on MPC servers
            // Full key is NEVER reconstructed - MPC servers use their shares directly
            match mpc_client.request_mpc_decryption(
                key_id, // Use key_id instead of full key
                &encrypted_result_hex,
                "fhe_engine",
            ).await {
                Ok(decrypted_value) => {
                    println!("[Rust FHE Engine] ✅ MPC threshold decryption successful: {}", decrypted_value);
                    decrypted_value == 1
                }
                Err(e) => {
                    eprintln!("[Rust FHE Engine] ❌ MPC threshold decryption failed: {}", e);
                    eprintln!("[Rust FHE Engine] ❌ Cannot fall back - threshold decryption required");
                    // Return error - no fallback to direct decryption
                    return (StatusCode::SERVICE_UNAVAILABLE, Json(EvaluationResponse { 
                        is_triggered: false 
                    }));
                }
            }
        } else {
            eprintln!("[Rust FHE Engine] ❌ MPC servers not available - threshold decryption required");
            return (StatusCode::SERVICE_UNAVAILABLE, Json(EvaluationResponse { 
                is_triggered: false 
            }));
        }
    } else if let Some(client_key_hex) = &payload.encrypted_client_key {
        // Fallback: Only if no MPC key_id and client_key provided (legacy support)
        println!("[Rust FHE Engine] ⚠️  No MPC key_id, using direct decryption (legacy mode)");
        simulate_tee_decryption(&encrypted_result, client_key_hex)
    } else {
        eprintln!("[Rust FHE Engine] ❌ No decryption method available - missing both key_id and client_key");
        return (StatusCode::BAD_REQUEST, Json(EvaluationResponse { 
            is_triggered: false 
        }));
    };

    println!("[Rust FHE Engine] Real FHE evaluation complete. Responding with 'is_triggered: {}'", is_triggered);
    (StatusCode::OK, Json(EvaluationResponse { is_triggered }))
}