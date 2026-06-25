//! Siphon decryptor — runs inside a Phala TDX enclave (dstack).
//!
//! Responsibility (intentionally tiny TCB):
//!   - hold each user's tfhe `ClientKey` IN MEMORY (received sealed to the enclave's attested key)
//!   - decrypt ONLY the fhe-engine's encrypted *result bit* → return { triggered: true|false }
//!
//! It must NEVER expose decrypted strategy inputs. The host/operator only ever sees ciphertext
//! and the boolean. See ../docs/PHALA_TEE_DECRYPTOR.md.

use std::collections::HashMap;
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use tfhe::integer::{ClientKey, RadixCiphertext};
use tokio::sync::RwLock;
use tower_http::cors::CorsLayer;

/// userId -> ClientKey, held only in enclave memory.
type KeyStore = Arc<RwLock<HashMap<String, ClientKey>>>;

#[derive(Clone)]
struct AppState {
    keys: KeyStore,
}

fn deserialize_hex<T: for<'de> serde::Deserialize<'de>>(hex_str: &str, label: &str) -> Result<T, String> {
    let bytes = hex::decode(hex_str).map_err(|e| format!("hex decode failed for {label}: {e}"))?;
    bincode::deserialize(&bytes).map_err(|e| format!("bincode deserialize failed for {label}: {e}"))
}

// ---------------------------------------------------------------------------
// /health
// ---------------------------------------------------------------------------
async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({ "service": "siphon-decryptor", "status": "healthy" }))
}

// ---------------------------------------------------------------------------
// GET /attestation
// TODO(attestation): return the dstack/TDX quote with report_data = sha256(enclave x25519 pubkey).
// The browser verifies the quote + pinned measurement, then encrypts the ClientKey to `pubkey`.
// dstack exposes the quote via its in-VM guest agent socket; bind report_data to our pubkey here.
// ---------------------------------------------------------------------------
async fn attestation() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "todo": "return TDX quote (dstack) with report_data = sha256(enclave_pubkey)",
        "enclave_pubkey": "<x25519 pubkey base64>"
    }))
}

// ---------------------------------------------------------------------------
// POST /clientKey   { userId, sealedClientKey }
// The ClientKey is sealed (HPKE/x25519) to the enclave's attested pubkey by the browser, so it
// is never visible to the host. TODO(attestation): unseal with the enclave private key.
// For local dev (no enclave) we accept a plain hex-encoded ClientKey behind a dev flag.
// ---------------------------------------------------------------------------
#[derive(Deserialize)]
struct ClientKeyReq {
    user_id: String,
    /// Sealed (prod) — or plain hex ClientKey when SIPHON_DEV_PLAINTEXT_KEY=1 (local only).
    client_key: String,
}

#[derive(Serialize)]
struct OkResp {
    ok: bool,
    error: Option<String>,
}

async fn has_client_key(State(st): State<AppState>, Path(user_id): Path<String>) -> Json<serde_json::Value> {
    let has = st.keys.read().await.contains_key(&user_id);
    Json(serde_json::json!({ "has_key": has }))
}

async fn set_client_key(State(st): State<AppState>, Json(req): Json<ClientKeyReq>) -> Json<OkResp> {
    let dev_plain = std::env::var("SIPHON_DEV_PLAINTEXT_KEY").as_deref() == Ok("1");

    let ck: ClientKey = if dev_plain {
        match deserialize_hex(&req.client_key, "client_key") {
            Ok(k) => k,
            Err(e) => return Json(OkResp { ok: false, error: Some(e) }),
        }
    } else {
        // TODO(attestation): unseal req.client_key with the enclave x25519 private key (HPKE),
        // then bincode-deserialize the ClientKey. Until then, prod path is not enabled.
        return Json(OkResp {
            ok: false,
            error: Some("sealed key unseal not wired yet (set SIPHON_DEV_PLAINTEXT_KEY=1 for local dev)".into()),
        });
    };

    st.keys.write().await.insert(req.user_id, ck);
    Json(OkResp { ok: true, error: None })
}

// ---------------------------------------------------------------------------
// POST /decrypt   { userId, encryptedResultHex } -> { triggered }
// The ONLY decryption path. Decrypts the engine's 0/1 result RadixCiphertext to a bool.
// ---------------------------------------------------------------------------
#[derive(Deserialize)]
struct DecryptReq {
    user_id: String,
    /// hex(bincode(RadixCiphertext)) — the engine's encrypted 0/1 result.
    encrypted_result: String,
}

#[derive(Serialize)]
struct DecryptResp {
    triggered: Option<bool>,
    error: Option<String>,
}

async fn decrypt(State(st): State<AppState>, Json(req): Json<DecryptReq>) -> Json<DecryptResp> {
    let keys = st.keys.read().await;
    let ck = match keys.get(&req.user_id) {
        Some(k) => k,
        None => return Json(DecryptResp { triggered: None, error: Some("no client key for user (upload it after attestation)".into()) }),
    };

    let ct: RadixCiphertext = match deserialize_hex(&req.encrypted_result, "encrypted_result") {
        Ok(c) => c,
        Err(e) => return Json(DecryptResp { triggered: None, error: Some(e) }),
    };

    // The engine encodes the result as a radix integer 1 (true) / 0 (false). NUM_BLOCKS in the
    // engine is 16; decrypt_radix infers from the ciphertext. Non-zero => triggered.
    let value: u64 = ck.decrypt_radix(&ct);
    Json(DecryptResp { triggered: Some(value != 0), error: None })
}

#[tokio::main]
async fn main() {
    let state = AppState { keys: Arc::new(RwLock::new(HashMap::new())) };

    let app = Router::new()
        .route("/health", get(health))
        .route("/attestation", get(attestation))
        .route("/hasClientKey/:user_id", get(has_client_key))
        .route("/clientKey", post(set_client_key))
        .route("/decrypt", post(decrypt))
        .layer(CorsLayer::permissive())
        .with_state(state);

    let port = std::env::var("PORT").unwrap_or_else(|_| "5002".into());
    let addr = format!("0.0.0.0:{port}");
    let listener = tokio::net::TcpListener::bind(&addr).await.expect("bind");
    println!("[siphon-decryptor] listening on {addr}");
    axum::serve(listener, app).await.expect("serve");
}
