from web3 import Web3


def connect_web3(rpc_url: str, timeout_sec: float = 10.0) -> Web3:
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout_sec}))
