use tfhe::integer::{gen_keys_radix, RadixCiphertext, RadixClientKey, ServerKey};
use tfhe::shortint::parameters::PARAM_MESSAGE_2_CARRY_2_KS_PBS;
use rand::SeedableRng;
use rand_chacha::ChaCha20Rng;
use hex;

const NUM_BLOCKS: usize = 16;

pub fn generate_fhe_keys() -> (RadixClientKey, ServerKey) {
    // Generates the cryptographic keys for FHE operations.
    gen_keys_radix(PARAM_MESSAGE_2_CARRY_2_KS_PBS, NUM_BLOCKS)
}

/// Generate FHE keys using a seed (for deterministic generation from MPC shared secret)
pub fn generate_fhe_keys_from_seed(_seed: &[u8; 32]) -> (RadixClientKey, ServerKey) {
    // Create a seeded RNG from the MPC shared secret
    // Note: tfhe-rs doesn't directly support seeded key generation,
    // so we'll use the standard generation but document that in production
    // you'd want to use a seeded CSPRNG
    // For now, the seed parameter is accepted but not used
    gen_keys_radix(PARAM_MESSAGE_2_CARRY_2_KS_PBS, NUM_BLOCKS)
}

/// Generate a seed from MPC public key set (deterministic)
pub fn derive_seed_from_mpc_pubkey(pubkey_hex: &str) -> Result<[u8; 32], String> {
    use sha2::{Sha256, Digest};
    
    // Hash the MPC public key to get a 32-byte seed
    let pubkey_bytes = hex::decode(pubkey_hex)
        .map_err(|e| format!("Failed to decode pubkey: {}", e))?;
    
    let mut hasher = Sha256::new();
    hasher.update(&pubkey_bytes);
    let hash = hasher.finalize();
    
    let mut seed = [0u8; 32];
    seed.copy_from_slice(&hash);
    Ok(seed)
}

pub fn encrypt_price(price_u32: u32, cks: &RadixClientKey) -> RadixCiphertext {
    // Encrypts the integer price (which holds price * 100)
    cks.encrypt(price_u32 as u64)
}
