import subprocess
import tempfile
import json
import os
from config import ZKEY_PATH, WASM_PATH, RAPIDSNARK_BIN, NODE_BIN, WITNESS_GEN_JS

REQUIRED_INPUTS = [
    'withdrawnValue', 'stateRoot', 'newCommitment', 'nullifierHash', 'recipient',
    'existingValue', 'existingNullifier', 'existingSecret',
    'newNullifier', 'newSecret', 'pathElements', 'pathIndices',
]

def generate_proof(inputs: dict) -> dict:
    """
    Run witness generation + rapidsnark.
    Returns { proof: {...}, publicSignals: [...] }
    Raises RuntimeError on any failure.
    """
    # Validate inputs
    missing = [k for k in REQUIRED_INPUTS if k not in inputs]
    if missing:
        raise ValueError(f"Missing circuit inputs: {missing}")

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path   = os.path.join(tmpdir, 'input.json')
        witness_path = os.path.join(tmpdir, 'witness.wtns')
        proof_path   = os.path.join(tmpdir, 'proof.json')
        public_path  = os.path.join(tmpdir, 'public.json')

        # Step 1: Write inputs
        with open(input_path, 'w') as f:
            json.dump(inputs, f)

        # Step 2: Generate witness via node
        witness_cmd = [NODE_BIN, WITNESS_GEN_JS, WASM_PATH, input_path, witness_path]
        result = subprocess.run(witness_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"Witness generation failed: {result.stderr}")

        # Step 3: Generate proof via rapidsnark
        prove_cmd = [RAPIDSNARK_BIN, ZKEY_PATH, witness_path, proof_path, public_path]
        result = subprocess.run(prove_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"rapidsnark failed: {result.stderr}")

        # Step 4: Read outputs
        with open(proof_path) as f:
            proof = json.load(f)
        with open(public_path) as f:
            public_signals = json.load(f)

    return {'proof': proof, 'publicSignals': public_signals}
