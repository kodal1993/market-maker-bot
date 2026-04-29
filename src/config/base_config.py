from dotenv import load_dotenv
import os


def load_base_config():
    load_dotenv()

    return {
        "BASE_RPC_URL": os.getenv("BASE_RPC_URL", "https://mainnet.base.org"),
        "CHAIN_ID": int(os.getenv("CHAIN_ID", 8453)),
        "WETH_ADDRESS": os.getenv("WETH_ADDRESS", "0x4200000000000000000000000000000000000006"),
        "USDC_ADDRESS": os.getenv("USDC_ADDRESS", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
        "DEX_TYPE": os.getenv("DEX_TYPE", "aerodrome"),
        "SLIPPAGE_TOLERANCE": float(os.getenv("SLIPPAGE_TOLERANCE", 0.005)),
        "PAPER_TRADING": os.getenv("PAPER_TRADING", "true").lower() == "true",
    }
