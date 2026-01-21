mod fhe_core; 
mod mpc_client;
mod key_sharing; 

use axum::{http::StatusCode, response::IntoResponse, routing::post, Json, Router};
use bincode;
use hex::encode;
use reqwest;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value}; 
use std::net::SocketAddr;
use std::env; // Import env to read environment variables
use tower_http::cors::{Any, CorsLayer};
use uuid::Uuid;
use mpc_client::MPCClient;

#[derive(Deserialize)]
struct StrategyInput {
    user_id: String,
    strategy_type: String,
    asset_in: String,
    asset_out: String,
    amount: f64,
    upper_bound: f64,
    lower_bound: f64,
    recipient_address: String,
    zk_proof: Value, 
}

#[derive(Serialize)]
struct StrategyPayload {
    user_id: String,
    strategy_type: String,
    asset_in: String,
    asset_out: String,
    amount: f64,
    recipient_address: String,
    zkp_data: String,
    encrypted_upper_bound: String,
    encrypted_lower_bound: String,
    server_key: String,
    encrypted_client_key: Option<String>, // Optional - only if shares not stored on MPC
    mpc_public_key_set: Option<String>, // MPC public key set for distributed key
    mpc_share_indices: Option<Vec<usize>>, // Which MPC servers hold shares
    fhe_key_id: Option<String>, // ID of the FHE key (shares stored on MPC servers)
    payload_id: String,
}

#[tokio::main]
async fn main() {
    // Load .env file manually (without dotenv crate due to network issues)
    // Try multiple locations: trade-executor/.env, current directory .env
    let env_paths = vec![
        std::path::Path::new("../trade-executor/.env"),
        std::path::Path::new(".env"),
        std::path::Path::new("../.env"),
    ];
    
    let mut env_loaded = false;
    for env_path in env_paths {
        if env_path.exists() {
            match std::fs::read_to_string(env_path) {
                Ok(contents) => {
                    // Parse .env file manually
                    for line in contents.lines() {
                        let line = line.trim();
                        // Skip comments and empty lines
                        if line.is_empty() || line.starts_with('#') {
                            continue;
                        }
                        // Parse KEY=VALUE format
                        if let Some(eq_pos) = line.find('=') {
                            let key = line[..eq_pos].trim();
                            let value = line[eq_pos + 1..].trim();
                            // Remove quotes if present
                            let value = value.trim_matches('"').trim_matches('\'');
                            if !key.is_empty() && !value.is_empty() {
                                std::env::set_var(key, value);
                            }
                        }
                    }
                    println!("✅ Loaded .env file from: {}", env_path.display());
                    env_loaded = true;
                    break;
                }
                Err(e) => {
                    println!("⚠️  Could not read .env from {}: {}", env_path.display(), e);
                }
            }
        }
    }
    
    if !env_loaded {
        println!("⚠️  Note: Could not load .env file. Using environment variables only.");
    }
    
    // Verify API_TOKEN is set
    if let Ok(token) = env::var("API_TOKEN") {
        println!("🔐 API_TOKEN is configured (length: {} chars)", token.len());
    } else {
        println!("⚠️  WARNING: API_TOKEN not set in environment. Authentication may fail.");
    }
    
    // CORS: Allow Frontend (siphon.money) to talk to this backend
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods([axum::http::Method::POST, axum::http::Method::GET])
        .allow_headers(Any);

    let app = Router::new()
        .route("/generatePayload", post(handle_generate_payload))
        .layer(cors);

    // PORT CONFIGURATION
    // Listen on 5009 (matches your frontend/docker setup)
    let port = 5009;
    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    
    println!("🚀 Payload Generator listening at http://{}", addr);
    println!("🔗 CORS enabled for all origins");

    axum::Server::bind(&addr)
        .serve(app.into_make_service())
        .await
        .unwrap();
}

async fn handle_generate_payload(Json(input): Json<StrategyInput>) -> impl IntoResponse {
    println!("🧠 Processing real ZK payload for user: {}", input.user_id);

    // 1️⃣ Generate FHE keys using MPC
    // Get MPC server URLs from environment or use defaults
    let mpc_server_1 = env::var("MPC_SERVER_1_URL").unwrap_or_else(|_| "http://localhost:8001".to_string());
    let mpc_server_2 = env::var("MPC_SERVER_2_URL").unwrap_or_else(|_| "http://localhost:8002".to_string());
    let mpc_client = MPCClient::new(vec![mpc_server_1.clone(), mpc_server_2.clone()]);
    
    // Check MPC server health
    let health = mpc_client.check_health().await;
    let all_healthy = health.values().all(|&h| h);
    
    println!("🔍 [MPC] Health check results:");
    for (url, is_healthy) in &health {
        println!("   {}: {}", url, if *is_healthy { "✅ Healthy" } else { "❌ Unavailable" });
    }
    println!("   Overall: {}", if all_healthy { "✅ All servers available - MPC mode" } else { "❌ Some servers unavailable - Legacy mode" });
    
    // Track whether shares were successfully stored (only use MPC mode if all shares stored)
    let mut shares_stored_successfully = false;
    
    let (client_key, server_key, mpc_pubkey_set, mpc_share_indices) = if all_healthy {
        println!("🔐 [MPC] All MPC servers healthy, requesting distributed key generation...");
        
        match mpc_client.request_key_generation(&input.user_id).await {
            Ok(mpc_response) => {
                if let Some(pubkey_hex) = mpc_response.public_key_set {
                    println!("✅ [MPC] Received MPC public key set, deriving FHE keys...");
                    
                    // Derive FHE keys from MPC public key (deterministic)
                    match fhe_core::derive_seed_from_mpc_pubkey(&pubkey_hex) {
                        Ok(_seed) => {
                            // Generate FHE keys
                            let (ck, sk) = fhe_core::generate_fhe_keys();
                            
                            // Split client key into shares for MPC servers
                            println!("🔐 [MPC] Splitting FHE client key into shares...");
                            match key_sharing::split_fhe_key(&ck, 2) {
                                Ok(shares) => {
                                    // Generate key ID (hash of full key) for reference
                                    let key_id = key_sharing::generate_key_id(&ck);
                                    
                                    // Store shares on MPC servers (distributed storage)
                                    println!("💾 [MPC] Storing key shares on MPC servers...");
                                    let mut all_stored = true;
                                    
                                    for (idx, share) in shares.iter().enumerate() {
                                        match mpc_client.store_key_share(&key_id, share, idx, &input.user_id, idx).await {
                                            Ok(_) => {
                                                println!("✅ [MPC] Share {} stored on MPC server {}", idx, idx + 1);
                                            }
                                            Err(e) => {
                                                eprintln!("⚠️  [MPC] Failed to store share {}: {}", idx, e);
                                                all_stored = false;
                                            }
                                        }
                                    }
                                    
                                    if all_stored {
                                        println!("✅ [MPC] All key shares stored on MPC servers");
                                        println!("   Key ID: {} (full key NOT stored in database)", key_id);
                                        shares_stored_successfully = true;
                                        // Store key_id instead of full key - shares are on MPC servers
                                        (ck, sk, Some(pubkey_hex), mpc_response.mpc_share_indices)
                                    } else {
                                        eprintln!("❌ [MPC] Some shares failed to store - MPC mode requires all shares");
                                        eprintln!("   Cannot proceed without MPC - rejecting request for security");
                                        // Return error - don't fall back to storing full key (security requirement)
                                        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({
                                            "status": "error",
                                            "message": "MPC share storage failed - cannot proceed without distributed key storage for security"
                                        })));
                                    }
                                }
                                Err(e) => {
                                    eprintln!("❌ [MPC] Failed to split key: {} - MPC mode requires key splitting", e);
                                    eprintln!("   Cannot proceed without MPC - rejecting request for security");
                                    // Return error - don't fall back to storing full key (security requirement)
                                    return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({
                                        "status": "error",
                                        "message": format!("MPC key splitting failed: {} - cannot proceed without distributed key storage", e)
                                    })));
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("⚠️  [MPC] Failed to derive seed: {}, falling back to direct generation", e);
                            let (ck, sk) = fhe_core::generate_fhe_keys();
                            (ck, sk, None, None)
                        }
                    }
                } else {
                    eprintln!("⚠️  [MPC] No public key set in response, falling back to direct generation");
                    let (ck, sk) = fhe_core::generate_fhe_keys();
                    (ck, sk, None, None)
                }
            }
            Err(e) => {
                eprintln!("⚠️  [MPC] Key generation failed: {}, falling back to direct generation", e);
                let (ck, sk) = fhe_core::generate_fhe_keys();
                (ck, sk, None, None)
            }
        }
    } else {
        println!("⚠️  [MPC] Some MPC servers unavailable, using direct key generation");
        println!("   Health status: {:?}", health);
        let (ck, sk) = fhe_core::generate_fhe_keys();
        (ck, sk, None, None)
    };

    // 2️⃣ Encrypt bounds
    let encrypted_upper = fhe_core::encrypt_price((input.upper_bound * 100.0) as u32, &client_key);
    let encrypted_lower = fhe_core::encrypt_price((input.lower_bound * 100.0) as u32, &client_key);

    // 3️⃣ Extract ZK Data
    let default_val = json!("0"); 
    
    // Extract standard ZK fields
    let proof = input.zk_proof.get("proof").unwrap_or(&default_val);
    let nullifier = input.zk_proof.get("nullifierHash").unwrap_or(&default_val);
    let new_commitment = input.zk_proof.get("newCommitment").unwrap_or(&default_val);
    
    // Extract Merkle Root
    let root = input.zk_proof.get("root")
        .or_else(|| input.zk_proof.get("stateRoot"))
        .unwrap_or(&default_val);
    
    // Extract Amount
    let amount_val = input.zk_proof.get("atomicAmount")
        .cloned()
        .unwrap_or_else(|| json!((input.amount * 1_000_000.0) as u64));

    // 4️⃣ Construct JSON for Python Executor
    let zkp_data_string = serde_json::to_string(&json!({
        "proof": proof,
        "publicInputs": {
            "root": root,             
            "nullifier": nullifier,
            "newCommitment": new_commitment,
            "asset": input.asset_in, 
            "amount": amount_val
        }
    }))
    .unwrap();

    // Generate key ID for reference (only if shares were successfully stored)
    let fhe_key_id = if shares_stored_successfully && mpc_pubkey_set.is_some() {
        Some(key_sharing::generate_key_id(&client_key))
    } else {
        None
    };
    
    // Only store full client key if NOT using MPC shares OR if shares failed to store
    // If using MPC and all shares stored successfully, we don't store the full key
    let encrypted_client_key = if shares_stored_successfully && mpc_pubkey_set.is_some() {
        None // Shares stored on MPC servers, don't store full key
    } else {
        Some(encode(bincode::serialize(&client_key).unwrap())) // Store full key (fallback or direct mode)
    };

    // Summary: Log which mode we're using
    if shares_stored_successfully && fhe_key_id.is_some() {
        println!("✅ [MPC] ✅ MPC MODE ENABLED - Using threshold decryption");
        println!("   - Key ID: {}", fhe_key_id.as_ref().unwrap());
        println!("   - Full client key: NOT stored (shares on MPC servers)");
        println!("   - Decryption: Will use MPC threshold protocol");
    } else {
        println!("⚠️  [MPC] ⚠️  LEGACY MODE - Using direct decryption");
        println!("   - Key ID: None");
        println!("   - Full client key: Stored in database (less secure)");
        println!("   - Decryption: Will use direct decryption (legacy)");
        if !all_healthy {
            println!("   - Reason: MPC servers unavailable");
        }
    }

    let payload = StrategyPayload {
        user_id: input.user_id.clone(),
        strategy_type: input.strategy_type.clone(),
        asset_in: input.asset_in.clone(),
        asset_out: input.asset_out.clone(),
        amount: input.amount,
        recipient_address: input.recipient_address.clone(),
        zkp_data: zkp_data_string,
        encrypted_upper_bound: encode(bincode::serialize(&encrypted_upper).unwrap()),
        encrypted_lower_bound: encode(bincode::serialize(&encrypted_lower).unwrap()),
        server_key: encode(bincode::serialize(&server_key).unwrap()),
        encrypted_client_key, // None if using MPC shares
        mpc_public_key_set: mpc_pubkey_set,
        mpc_share_indices: mpc_share_indices,
        fhe_key_id, // Key ID for reference when shares are on MPC
        payload_id: Uuid::new_v4().to_string(),
    };

    // 5️⃣ Send to Python Orchestrator (Dynamic URL!)
    // If running in Docker on Mac, use host.docker.internal
    // If running in Cloud (Render/Fluence), use the ENV variable
    let default_url = "http://localhost:5005/createStrategy";
    let orchestrator_url = env::var("ORCHESTRATOR_URL").unwrap_or_else(|_| default_url.to_string());
    
    println!("➡️  Forwarding to Orchestrator at: {}", orchestrator_url);

    let client = reqwest::Client::new();

    // Get API token from environment variable
    let api_token = env::var("API_TOKEN").ok();
    let mut request = client.post(&orchestrator_url).json(&payload);
    
    // Add authentication header if token is available
    if let Some(token) = &api_token {
        request = request.header("X-API-TOKEN", token);
        println!("🔐 Using API token for authentication");
    } else {
        println!("⚠️  WARNING: API_TOKEN not set, request may be rejected");
    }

    match request.send().await {
        Ok(res) => {
            let status = res.status();
            if status.is_success() {
                println!("✅ Forwarded real ZK data to Python orchestrator");
                (StatusCode::OK, Json(json!({"status": "success", "payload": payload})))
            } else {
                let text = res.text().await.unwrap_or_default();
                eprintln!("❌ Orchestrator error: {}", text);
                (status, Json(json!({"status": "error", "message": text})))
            }
        }
        Err(e) => {
            eprintln!("❌ Failed to reach orchestrator at {}: {}", orchestrator_url, e);
            (StatusCode::BAD_GATEWAY, Json(json!({"status": "error", "details": e.to_string()})))
        }
    }
}