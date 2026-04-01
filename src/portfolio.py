class Portfolio:
    def __init__(self, usdc: float, eth: float):
        self.usdc = usdc
        self.eth = eth
        self.fees_paid_usd = 0.0
        self.realized_pnl_usd = 0.0
        self.eth_cost_basis = None

    def inventory_usd(self, mid_price: float) -> float:
        return self.eth * mid_price

    def total_equity_usd(self, mid_price: float) -> float:
        return self.usdc + (self.eth * mid_price)

    def ensure_cost_basis(self, reference_price: float) -> None:
        if self.eth > 0 and self.eth_cost_basis is None:
            self.eth_cost_basis = reference_price

    def min_profitable_sell_price(self, fee_bps: float, profit_bps: float) -> float | None:
        if self.eth <= 0 or self.eth_cost_basis is None:
            return None

        fee_rate = fee_bps / 10000.0
        if fee_rate >= 1.0:
            return None

        target_multiplier = 1.0 + (profit_bps / 10000.0)
        return (self.eth_cost_basis * target_multiplier) / (1.0 - fee_rate)

    def buy_eth(self, price: float, size_base: float, fee_usd: float) -> None:
        current_cost = 0.0 if self.eth_cost_basis is None else self.eth * self.eth_cost_basis
        cost = price * size_base
        added_cost = cost + fee_usd
        new_eth = self.eth + size_base

        self.usdc -= (cost + fee_usd)
        self.eth = new_eth
        self.fees_paid_usd += fee_usd

        if new_eth > 0:
            self.eth_cost_basis = (current_cost + added_cost) / new_eth

    def sell_eth(self, price: float, size_base: float, fee_usd: float) -> None:
        proceeds = price * size_base
        net_proceeds = proceeds - fee_usd
        cost_basis = 0.0 if self.eth_cost_basis is None else self.eth_cost_basis
        self.realized_pnl_usd += net_proceeds - (cost_basis * size_base)

        self.usdc += net_proceeds
        self.eth -= size_base
        self.fees_paid_usd += fee_usd

        if self.eth <= 1e-12:
            self.eth = 0.0
            self.eth_cost_basis = None
