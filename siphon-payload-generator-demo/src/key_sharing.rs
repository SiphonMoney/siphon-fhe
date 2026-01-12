// Threshold Key Sharing for FHE Client Keys
// Splits FHE client keys into shares using Shamir Secret Sharing
// Shares are stored on MPC servers, full key is never reconstructed

use tfhe::integer::RadixClientKey;
use bincode;
use hex;
use sha2::{Sha256, Digest};
use rand::Rng;

/// Split FHE client key into shares using deterministic secret sharing
/// Returns shares that can be distributed to MPC servers
pub fn split_fhe_key(key: &RadixClientKey, num_shares: usize) -> Result<Vec<String>, String> {
    // Serialize the key
    let key_bytes = bincode::serialize(key)
        .map_err(|e| format!("Failed to serialize key: {}", e))?;
    
    // For a 2-of-2 threshold, we split into 2 shares
    // In production, use proper secret sharing library
    // For now, we'll use a simple XOR-based splitting
    
    if num_shares != 2 {
        return Err("Currently only supports 2-of-2 threshold".to_string());
    }
    
    // Generate random mask for share 1
    let mut rng = rand::thread_rng();
    let mask: Vec<u8> = (0..key_bytes.len())
        .map(|_| rng.gen::<u8>())
        .collect();
    
    // Share 1 = mask
    let share1 = mask.clone();
    
    // Share 2 = key XOR mask
    let share2: Vec<u8> = key_bytes.iter()
        .zip(mask.iter())
        .map(|(a, b)| a ^ b)
        .collect();
    
    // Encode shares as hex
    Ok(vec![
        hex::encode(&share1),
        hex::encode(&share2),
    ])
}

/// Reconstruct FHE client key from shares (only for verification, not used in production)
/// In true MPC, we never reconstruct the full key
pub fn reconstruct_fhe_key(shares: &[String]) -> Result<RadixClientKey, String> {
    if shares.len() != 2 {
        return Err("Need exactly 2 shares for 2-of-2 threshold".to_string());
    }
    
    // Decode shares
    let share1_bytes = hex::decode(&shares[0])
        .map_err(|e| format!("Failed to decode share 1: {}", e))?;
    let share2_bytes = hex::decode(&shares[1])
        .map_err(|e| format!("Failed to decode share 2: {}", e))?;
    
    if share1_bytes.len() != share2_bytes.len() {
        return Err("Share lengths don't match".to_string());
    }
    
    // Reconstruct: key = share1 XOR share2
    let key_bytes: Vec<u8> = share1_bytes.iter()
        .zip(share2_bytes.iter())
        .map(|(a, b)| a ^ b)
        .collect();
    
    // Deserialize back to RadixClientKey
    bincode::deserialize(&key_bytes)
        .map_err(|e| format!("Failed to deserialize key: {}", e))
}

/// Generate a key ID from the key (for reference without storing full key)
pub fn generate_key_id(key: &RadixClientKey) -> String {
    let key_bytes = bincode::serialize(key).unwrap();
    let hash = Sha256::digest(&key_bytes);
    hex::encode(&hash[..16]) // Use first 16 bytes as ID
}
