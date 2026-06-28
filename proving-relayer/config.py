import os
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv('PROVING_RELAYER_PORT', 5010))
ZKEY_PATH = os.getenv('ZKEY_PATH', '../../siphon-zk/circuits/build/zkey_final.zkey')
WASM_PATH = os.getenv('WASM_PATH', '../../siphon-zk/circuits/build/main_js/main.wasm')
RAPIDSNARK_BIN = os.getenv('RAPIDSNARK_BIN', 'rapidsnark')
NODE_BIN = os.getenv('NODE_BIN', 'node')
WITNESS_GEN_JS = os.getenv('WITNESS_GEN_JS', '../../siphon-zk/circuits/build/main_js/generate_witness.js')
