mod fhe_core; 

use axum::{http::StatusCode, response::IntoResponse, routing::{post, get}, Json, Router};
use bincode;
use hex::encode;
use reqwest;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value}; 
use std::net::SocketAddr;
use std::env;
use tower_http::cors::{Any, CorsLayer};
use uuid::Uuid;

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
    encrypted_client_key: String,
    payload_id: String,
}

#[tokio::main]
async fn main() {
    // Load .env file manually
    let env_paths = vec![
        std::path::Path::new("../trade-executor/.env"),
        std::path::Path::new(".env"),
        std::path::Path::new("../.env"),
    ];
    
    for env_path in env_paths {
        if env_path.exists() {
            if let Ok(contents) = std::fs::read_to_string(env_path) {
                for line in contents.lines() {
                    let line = line.trim();
                    if line.is_empty() || line.starts_with('#') {
                        continue;
                    }
                    if let Some(eq_pos) = line.find('=') {
                        let key = line[..eq_pos].trim();
                        let value = line[eq_pos + 1..].trim();
                        let value = value.trim_matches('"').trim_matches('\'');
                        if !key.is_empty() && !value.is_empty() {
                            std::env::set_var(key, value);
                        }
                    }
                }
                println!("✅ Loaded .env file from: {}", env_path.display());
                break;
            }
        }
    }
    
    // CORS configuration
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods([axum::http::Method::POST, axum::http::Method::GET])
        .allow_headers(Any);

    let app = Router::new()
        .route("/generatePayload", post(handle_generate_payload))
        .route("/health", get(health_check))
        .layer(cors);

    let port = 5009;
    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    
    println!("🚀 Payload Generator listening at http://{}", addr);

    axum::Server::bind(&addr)
        .serve(app.into_make_service())
        .await
        .unwrap();
}

async fn health_check() -> impl IntoResponse {
    Json(json!({"status": "healthy", "service": "payload-generator"}))
}

async fn handle_generate_payload(Json(input): Json<StrategyInput>) -> impl IntoResponse {
    println!("🧠 Processing payload for user: {}", input.user_id);

    // 1️⃣ Generate FHE keys
    println!("🔐 Generating FHE keys...");
    let (client_key, server_key) = fhe_core::generate_fhe_keys();
    println!("✅ FHE keys generated");

    // 2️⃣ Encrypt bounds
    let encrypted_upper = fhe_core::encrypt_price((input.upper_bound * 100.0) as u32, &client_key);
    let encrypted_lower = fhe_core::encrypt_price((input.lower_bound * 100.0) as u32, &client_key);

    // 3️⃣ Extract ZK Data
    let default_val = json!("0"); 
    
    let proof = input.zk_proof.get("proof").unwrap_or(&default_val);
    let nullifier = input.zk_proof.get("nullifierHash").unwrap_or(&default_val);
    let new_commitment = input.zk_proof.get("newCommitment").unwrap_or(&default_val);
    
    let root = input.zk_proof.get("root")
        .or_else(|| input.zk_proof.get("stateRoot"))
        .unwrap_or(&default_val);
    
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
        encrypted_client_key: encode(bincode::serialize(&client_key).unwrap()),
        payload_id: Uuid::new_v4().to_string(),
    };

    // 5️⃣ Send to Python Orchestrator
    let default_url = "http://localhost:5005/createStrategy";
    let orchestrator_url = env::var("ORCHESTRATOR_URL").unwrap_or_else(|_| default_url.to_string());
    
    println!("➡️  Forwarding to Orchestrator at: {}", orchestrator_url);

    let client = reqwest::Client::new();
    let request = client.post(&orchestrator_url).json(&payload);

    match request.send().await {
        Ok(res) => {
            let status = res.status();
            if status.is_success() {
                println!("✅ Forwarded payload to Python orchestrator");
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