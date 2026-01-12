// MPC Client for FHE Engine to communicate with MPC4ALL servers for decryption

use serde::{Deserialize, Serialize};
use reqwest;
use std::env;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MPCDecryptRequest {
    pub request_id: String,
    pub key_id: String, // Identifier for the FHE key (shares stored on MPC servers)
    pub encrypted_result: String, // hex encoded encrypted FHE computation result
    pub user_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MPCDecryptResponse {
    pub success: bool,
    pub decrypted_value: Option<u64>, // The decrypted FHE result
    pub node_id: usize,
    pub message: Option<String>,
}

pub struct MPCClient {
    server_urls: Vec<String>,
    client: reqwest::Client,
}

impl MPCClient {
    pub fn new() -> Self {
        // Get MPC server URLs from environment or use defaults
        let server_1 = env::var("MPC_SERVER_1_URL").unwrap_or_else(|_| "http://localhost:8001".to_string());
        let server_2 = env::var("MPC_SERVER_2_URL").unwrap_or_else(|_| "http://localhost:8002".to_string());
        
        Self {
            server_urls: vec![server_1, server_2],
            client: reqwest::Client::new(),
        }
    }

    /// Request MPC servers to perform threshold decryption
    /// Uses key shares stored on MPC servers (full key never reconstructed)
    pub async fn request_mpc_decryption(
        &self,
        key_id: &str, // Key ID instead of full key
        encrypted_result: &str,
        user_id: &str,
    ) -> Result<u64, String> {
        let request_id = uuid::Uuid::new_v4().to_string();
        
        println!("🔓 [MPC Client] Requesting MPC decryption from {} servers...", self.server_urls.len());
        
        // Try each MPC server until we get a successful decryption
        // In a 2-of-2 scheme, we need at least one server to respond
        for (idx, server_url) in self.server_urls.iter().enumerate() {
            let req = MPCDecryptRequest {
                request_id: request_id.clone(),
                key_id: key_id.to_string(),
                encrypted_result: encrypted_result.to_string(),
                user_id: user_id.to_string(),
            };

            println!("🔓 [MPC Client] Requesting decryption from MPC server {}: {}", idx + 1, server_url);
            
            match self.client
                .post(&format!("{}/decryptFHE", server_url))
                .json(&req)
                .timeout(std::time::Duration::from_secs(30))
                .send()
                .await
            {
                Ok(resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        match resp.json::<MPCDecryptResponse>().await {
                            Ok(decrypt_resp) => {
                                if decrypt_resp.success {
                                    if let Some(decrypted_value) = decrypt_resp.decrypted_value {
                                        println!("✅ [MPC Client] Successfully decrypted via MPC server {}: {}", idx + 1, decrypted_value);
                                        return Ok(decrypted_value);
                                    } else {
                                        println!("⚠️  [MPC Client] Server {} returned success but no decrypted value", idx + 1);
                                    }
                                } else {
                                    println!("⚠️  [MPC Client] Server {} returned failure: {:?}", idx + 1, decrypt_resp.message);
                                }
                            }
                            Err(e) => {
                                eprintln!("⚠️  [MPC Client] Failed to parse response from server {}: {}", idx + 1, e);
                            }
                        }
                    } else {
                        let status_code = resp.status();
                        let text = resp.text().await.unwrap_or_default();
                        eprintln!("⚠️  [MPC Client] Server {} returned error {}: {}", idx + 1, status_code, text);
                    }
                }
                Err(e) => {
                    eprintln!("⚠️  [MPC Client] Failed to connect to server {}: {}", idx + 1, e);
                }
            }
        }

        Err("Failed to decrypt via any MPC server".to_string())
    }

    /// Check if MPC servers are available
    pub async fn check_health(&self) -> bool {
        for server_url in &self.server_urls {
            if let Ok(resp) = self.client
                .get(&format!("{}/health", server_url))
                .timeout(std::time::Duration::from_secs(5))
                .send()
                .await
            {
                if resp.status().is_success() {
                    return true; // At least one server is healthy
                }
            }
        }
        false
    }
}
