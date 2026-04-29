import asyncio

from src.execution.dex_executor import DexExecutor


def test_simulate_swap_buy_uses_usdc_to_weth_direction(capsys):
    executor = DexExecutor.__new__(DexExecutor)
    executor.weth = "WETH"
    executor.usdc = "USDC"
    executor.slippage_tolerance = 0.005
    executor.default_gas_estimate = 230000

    captured = {}

    def fake_to_wei_amount(amount_in, token):
        captured["to_wei_token"] = token
        return 100

    async def fake_get_quote(amount_in, token_in, token_out):
        captured["token_in"] = token_in
        captured["token_out"] = token_out
        return {"amount_out": 50, "gas_estimate": 210000}

    def fake_from_base_units(amount, token):
        return 1.0

    def fake_calculate_gas_cost(gas_estimate):
        return 0.0

    executor._to_wei_amount = fake_to_wei_amount
    executor.get_quote = fake_get_quote
    executor._from_base_units = fake_from_base_units
    executor.calculate_gas_cost = fake_calculate_gas_cost

    result = asyncio.run(executor.simulate_swap(amount_in=1.0, is_buy=True))

    assert captured["to_wei_token"] == "USDC"
    assert captured["token_in"] == "USDC"
    assert captured["token_out"] == "WETH"
    assert result["input_token"] == "USDC"
    assert result["output_token"] == "WETH"

    out = capsys.readouterr().out
    assert "Simulating BUY ETH with USDC" in out


def test_simulate_swap_sell_uses_weth_to_usdc_direction(capsys):
    executor = DexExecutor.__new__(DexExecutor)
    executor.weth = "WETH"
    executor.usdc = "USDC"
    executor.slippage_tolerance = 0.005
    executor.default_gas_estimate = 230000

    captured = {}

    def fake_to_wei_amount(amount_in, token):
        captured["to_wei_token"] = token
        return 100

    async def fake_get_quote(amount_in, token_in, token_out):
        captured["token_in"] = token_in
        captured["token_out"] = token_out
        return {"amount_out": 50, "gas_estimate": 210000}

    def fake_from_base_units(amount, token):
        return 1.0

    def fake_calculate_gas_cost(gas_estimate):
        return 0.0

    executor._to_wei_amount = fake_to_wei_amount
    executor.get_quote = fake_get_quote
    executor._from_base_units = fake_from_base_units
    executor.calculate_gas_cost = fake_calculate_gas_cost

    result = asyncio.run(executor.simulate_swap(amount_in=1.0, is_buy=False))

    assert captured["to_wei_token"] == "WETH"
    assert captured["token_in"] == "WETH"
    assert captured["token_out"] == "USDC"
    assert result["input_token"] == "WETH"
    assert result["output_token"] == "USDC"

    out = capsys.readouterr().out
    assert "Simulating SELL ETH for USDC" in out
