import os
from dotenv import load_dotenv

load_dotenv()

PORT           = int(os.getenv('PROVING_RELAYER_PORT', 5010))
ZK_BUILD_DIR   = os.getenv('ZK_BUILD_DIR', '../../siphon-zk/circuits/build')
RAPIDSNARK_BIN = os.getenv('RAPIDSNARK_BIN', 'rapidsnark')
NODE_BIN       = os.getenv('NODE_BIN', 'node')

VALID_CIRCUITS = {
    'w1', 'w2', 'w3', 'w4', 'w5', 'w6',
    'm2', 'm3', 'm4', 'm5', 'm6',
}

def circuit_paths(circuit: str) -> tuple[str, str, str]:
    """Return (wasm_path, zkey_path, witness_gen_js) for a given circuit name."""
    if circuit not in VALID_CIRCUITS:
        raise ValueError(f"Unknown circuit '{circuit}'. Valid: {sorted(VALID_CIRCUITS)}")
    prefix = f'main_{circuit}'
    base   = os.path.join(ZK_BUILD_DIR, circuit, f'{prefix}_js')
    return (
        os.path.join(base, f'{prefix}.wasm'),
        os.path.join(ZK_BUILD_DIR, circuit, 'zkey_final.zkey'),
        os.path.join(base, 'generate_witness.js'),
    )
