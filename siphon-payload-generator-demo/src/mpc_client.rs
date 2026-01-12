// MPC Client for communicating with MPC4ALL servers
// This module handles distributed key generation for FHE client keys

use serde::{Deserialize, Serialize};
use reqwest;
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MPCKeyGenRequest {
    pub request_id: String,
    pub user_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MPCKeyGenResponse {
    pub success: bool,
    pub message: String,
    pub public_key_set: Option<String>, // hex encoded public key set
    pub mpc_share_indices: Option<Vec<usize>>, // which MPC servers hold shares
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoreKeyShareRequest {
    pub request_id: String,
    pub key_id: String, // Identifier for this key (hash of full key)
    pub key_share: String, // hex encoded key share
    pub share_index: usize, // Which share this is (0 or 1 for 2-of-2)
    pub user_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MPCDecryptRequest {
    pub request_id: String,
    pub encrypted_data: String, // hex encoded encrypted FHE result
    pub user_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MPCDecryptResponse {
    pub success: bool,
    pub decrypted_value: Option<u64>,
    pub partial_decryptions: Option<Vec<PartialDecryption>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PartialDecryption {
    pub node_id: usize,
    pub partial_result: String, // hex encoded partial decryption
}

pub struct MPCClient {
    server_urls: Vec<String>,
    client: reqwest::Client,
}

impl MPCClient {
    pub fn new(server_urls: Vec<String>) -> Self {
        Self {
            server_urls,
            client: reqwest::Client::new(),
        }
    }

    /// Request MPC servers to generate a shared secret for FHE key seeding
    pub async fn request_key_generation(
        &self,
        user_id: &str,
    ) -> Result<MPCKeyGenResponse, String> {
        let request_id = uuid::Uuid::new_v4().to_string();
        
        // Try to register/get keys from first available MPC server
        // The MPC servers will coordinate DKG among themselves
        let first_server = self.server_urls.first()
            .ok_or("No MPC servers configured")?;
        
        let req = MPCKeyGenRequest {
            request_id: request_id.clone(),
            user_id: user_id.to_string(),
        };

        println!("🔐 [MPC Client] Requesting key generation from MPC server: {}", first_server);
        
        match self.client
            .post(&format!("{}/generateFHEKey", first_server))
            .json(&req)
            .send()
            .await
        {
            Ok(resp) => {
                let status = resp.status();
                if status.is_success() {
                    let response: MPCKeyGenResponse = resp.json().await
                        .map_err(|e| format!("Failed to parse response: {}", e))?;
                    println!("✅ [MPC Client] Key generation response received");
                    Ok(response)
                } else {
                    let text = resp.text().await.unwrap_or_default();
                    Err(format!("MPC server error: {} - {}", status, text))
                }
            }
            Err(e) => {
                Err(format!("Failed to connect to MPC server: {}", e))
            }
        }
    }

    /// Request MPC servers to participate in decryption
    pub async fn request_decryption(
        &self,
        encrypted_data: &str,
        user_id: &str,
    ) -> Result<MPCDecryptResponse, String> {
        let request_id = uuid::Uuid::new_v4().to_string();
        
        // Request partial decryptions from all MPC servers
        let mut partial_results = Vec::new();
        
        for (idx, server_url) in self.server_urls.iter().enumerate() {
            let req = MPCDecryptRequest {
                request_id: request_id.clone(),
                encrypted_data: encrypted_data.to_string(),
                user_id: user_id.to_string(),
            };

            println!("🔓 [MPC Client] Requesting partial decryption from MPC server {}: {}", idx + 1, server_url);
            
            match self.client
                .post(&format!("{}/decryptFHE", server_url))
                .json(&req)
                .send()
                .await
            {
                Ok(resp) => {
                    if resp.status().is_success() {
                        let partial: PartialDecryption = resp.json().await
                            .map_err(|e| format!("Failed to parse partial decryption: {}", e))?;
                        partial_results.push(partial);
                        println!("✅ [MPC Client] Received partial decryption from server {}", idx + 1);
                    } else {
                        let text = resp.text().await.unwrap_or_default();
                        eprintln!("⚠️  [MPC Client] Server {} returned error: {}", idx + 1, text);
                    }
                }
                Err(e) => {
                    eprintln!("⚠️  [MPC Client] Failed to connect to server {}: {}", idx + 1, e);
                }
            }
        }

        if partial_results.len() < 2 {
            return Err(format!("Not enough partial decryptions: need 2, got {}", partial_results.len()));
        }

        // Combine partial decryptions (simplified - in production, use proper threshold decryption)
        // For now, we'll return the partial results and let the FHE engine combine them
        Ok(MPCDecryptResponse {
            success: true,
            decrypted_value: None, // Will be computed by combining partials
            partial_decryptions: Some(partial_results),
        })
    }

    /// Store FHE key share on MPC server
    /// This distributes the key share so the full key is never stored together
    pub async fn store_key_share(
        &self,
        key_id: &str,
        key_share: &str,
        share_index: usize,
        user_id: &str,
        server_index: usize,
    ) -> Result<bool, String> {
        if server_index >= self.server_urls.len() {
            return Err("Invalid server index".to_string());
        }
        
        let server_url = &self.server_urls[server_index];
        let request_id = uuid::Uuid::new_v4().to_string();
        
        let req = StoreKeyShareRequest {
            request_id,
            key_id: key_id.to_string(),
            key_share: key_share.to_string(),
            share_index,
            user_id: user_id.to_string(),
        };
        
        println!("💾 [MPC Client] Storing key share {} on MPC server {}: {}", share_index, server_index + 1, server_url);
        
        match self.client
            .post(&format!("{}/storeFHEKeyShare", server_url))
            .json(&req)
            .timeout(std::time::Duration::from_secs(10))
            .send()
            .await
        {
            Ok(resp) => {
                let status = resp.status();
                if status.is_success() {
                    println!("✅ [MPC Client] Key share stored on server {}", server_index + 1);
                    Ok(true)
                } else {
                    let text = resp.text().await.unwrap_or_else(|_| format!("HTTP {}", status));
                    Err(format!("Server {} error ({}): {}", server_index + 1, status, text))
                }
            }
            Err(e) => {
                let error_msg = if e.is_timeout() {
                    format!("Timeout connecting to server {}", server_index + 1)
                } else if e.is_connect() {
                    format!("Connection refused - server {} may not be running", server_index + 1)
                } else {
                    format!("Failed to connect to server {}: {}", server_index + 1, e)
                };
                Err(error_msg)
            }
        }
    }

    /// Check if MPC servers are available
    pub async fn check_health(&self) -> HashMap<String, bool> {
        let mut health_status = HashMap::new();
        
        for server_url in &self.server_urls {
            let is_healthy = match self.client
                .get(&format!("{}/health", server_url))
                .send()
                .await
            {
                Ok(resp) => resp.status().is_success(),
                Err(_) => false,
            };
            health_status.insert(server_url.clone(), is_healthy);
        }
        
        health_status
    }
}
