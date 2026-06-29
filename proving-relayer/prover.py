import subprocess
import tempfile
import json
import os
from config import RAPIDSNARK_BIN, NODE_BIN, circuit_paths


def generate_proof(inputs: dict, circuit: str) -> dict:
    """
    Run witness generation + rapidsnark for the given circuit (e.g. 'w1', 'm2').
    Returns { proof: {...}, publicSignals: [...] }
    Raises ValueError on bad inputs, RuntimeError on execution failure.
    """
    wasm_path, zkey_path, witness_gen_js = circuit_paths(circuit)

    for path, label in [(wasm_path, 'wasm'), (zkey_path, 'zkey'), (witness_gen_js, 'witness_gen_js')]:
        if not os.path.exists(path):
            raise RuntimeError(f"Circuit artifact not found ({label}): {path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path   = os.path.join(tmpdir, 'input.json')
        witness_path = os.path.join(tmpdir, 'witness.wtns')
        proof_path   = os.path.join(tmpdir, 'proof.json')
        public_path  = os.path.join(tmpdir, 'public.json')

        with open(input_path, 'w') as f:
            json.dump(inputs, f)

        result = subprocess.run(
            [NODE_BIN, witness_gen_js, wasm_path, input_path, witness_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Witness generation failed: {result.stderr}")

        result = subprocess.run(
            [RAPIDSNARK_BIN, zkey_path, witness_path, proof_path, public_path],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"rapidsnark failed: {result.stderr}")

        with open(proof_path) as f:
            proof = json.load(f)
        with open(public_path) as f:
            public_signals = json.load(f)

    return {'proof': proof, 'publicSignals': public_signals}
