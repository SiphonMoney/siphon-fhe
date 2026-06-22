use tfhe::integer::{RadixCiphertext, ServerKey};

const NUM_BLOCKS: usize = 16;

/// Performs a homomorphic comparison and returns an encrypted integer (1 for true, 0 for false).
/// current_price_cents is u64 to support asset prices above $42k (u32 overflows at ~$42.9M cents).
pub fn homomorphic_check(
    sks: &ServerKey,
    encrypted_trigger_price: &RadixCiphertext,
    condition: &str,
    current_price_cents: u64,
) -> Result<RadixCiphertext, String> {
    let encrypted_bool_result = match condition {
        "GTE" => sks.scalar_le_parallelized(encrypted_trigger_price, current_price_cents),
        "LTE" => sks.scalar_ge_parallelized(encrypted_trigger_price, current_price_cents),
        _ => return Err(format!("Invalid condition: {}", condition)),
    };

    Ok(sks.if_then_else_parallelized(
        &encrypted_bool_result,
        &sks.create_trivial_radix(1, NUM_BLOCKS),
        &sks.create_trivial_radix(0, NUM_BLOCKS),
    ))
}

pub fn homomorphic_or(
    sks: &ServerKey,
    a: &RadixCiphertext,
    b: &RadixCiphertext,
) -> RadixCiphertext {
    sks.bitor_parallelized(a, b)
}

pub fn homomorphic_and(
    sks: &ServerKey,
    a: &RadixCiphertext,
    b: &RadixCiphertext,
) -> RadixCiphertext {
    sks.bitand_parallelized(a, b)
}

/// Logical NOT on a 0/1-valued ciphertext: flips the low bit (1 XOR x).
pub fn homomorphic_not(sks: &ServerKey, a: &RadixCiphertext) -> RadixCiphertext {
    let one = sks.create_trivial_radix(1u64, NUM_BLOCKS);
    sks.bitxor_parallelized(a, &one)
}

