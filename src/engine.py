import random
from config import TREND_BUY_FILL_BONUS, TREND_SELL_FILL_BONUS
from types_bot import PaperOrder, FillResult


class PaperEngine:
    def __init__(self, portfolio, maker_fee_bps, taker_fee_bps):
        self.portfolio = portfolio
        self.trade_count = 0
        self.maker_fee_bps = maker_fee_bps
        self.taker_fee_bps = taker_fee_bps
        self.min_eth_reserve = 0.03

        self.buy_count = 0
        self.sell_count = 0

    def create_orders(self, bid, ask, usd, mode):
        return (
            PaperOrder("buy", bid, usd / bid, usd, self.maker_fee_bps, mode),
            PaperOrder("sell", ask, usd / ask, usd, self.maker_fee_bps, mode),
        )

    def create_order_from_decision(self, action, quoted_price, usd, mode):
        if action not in {"BUY", "SELL"} or quoted_price <= 0 or usd <= 0:
            side = "buy" if action == "BUY" else "sell"
            return PaperOrder(side, max(quoted_price, 0.0), 0.0, 0.0, self.maker_fee_bps, mode)

        side = "buy" if action == "BUY" else "sell"
        size_base = usd / quoted_price
        return PaperOrder(side, quoted_price, size_base, usd, self.maker_fee_bps, mode)

    def can_place_buy(self, max_inv, mid, usd, mode):
        if mode not in {"TREND_UP", "RANGE_MAKER"}:
            return False
        projected_inventory = self.portfolio.inventory_usd(mid) + usd
        return projected_inventory <= max_inv

    def can_place_sell(self, order, mode):
        if mode not in {"TREND_UP", "RANGE_MAKER", "OVERWEIGHT_EXIT"}:
            return False
        remaining_eth = self.portfolio.eth - order.size_base
        return remaining_eth >= (self.min_eth_reserve - 1e-9)

    def calculate_fill_probability(self, order, mid):
        if order.execution_type == "taker":
            return 0.999

        if order.side == "buy" and order.price >= mid:
            return 0.995
        if order.side == "sell" and order.price <= mid:
            return 0.995

        distance_bps = (abs(order.price - mid) / mid) * 10000.0

        if order.side == "buy":
            base_prob = 0.58
            decay_per_bps = 0.035
            if order.mode == "TREND_UP":
                base_prob += max(TREND_BUY_FILL_BONUS, 0.0)
            elif order.mode == "RANGE_MAKER":
                base_prob += 0.03
        else:
            base_prob = 0.56
            decay_per_bps = 0.032
            if order.mode == "OVERWEIGHT_EXIT":
                base_prob += 0.02
            elif order.mode == "TREND_UP":
                base_prob += max(TREND_SELL_FILL_BONUS, 0.0)
            elif order.mode == "RANGE_MAKER":
                base_prob += 0.025

        fill_prob = base_prob - (distance_bps * decay_per_bps)
        return max(min(fill_prob, 0.995), 0.03)

    def simulate_fill(self, order, mid):
        fill_prob = self.calculate_fill_probability(order, mid)
        filled = random.random() < fill_prob
        fee = order.size_usd * (order.fee_bps / 10000)

        if not filled:
            return FillResult(
                False,
                order.side,
                order.price,
                order.size_base,
                order.size_usd,
                0.0,
                "no fill",
                order.execution_type,
                order.slippage_bps,
                order.trade_reason,
            )

        if order.side == "buy":
            required = (order.price * order.size_base) + fee
            if self.portfolio.usdc < required:
                return FillResult(
                    False,
                    order.side,
                    order.price,
                    order.size_base,
                    order.size_usd,
                    0.0,
                    "no usdc",
                    order.execution_type,
                    order.slippage_bps,
                    order.trade_reason,
                )
            self.portfolio.buy_eth(order.price, order.size_base, fee)
            self.buy_count += 1

        else:
            remaining_eth = self.portfolio.eth - order.size_base
            if self.portfolio.eth < order.size_base or remaining_eth < (self.min_eth_reserve - 1e-9):
                return FillResult(
                    False,
                    order.side,
                    order.price,
                    order.size_base,
                    order.size_usd,
                    0.0,
                    "protect eth",
                    order.execution_type,
                    order.slippage_bps,
                    order.trade_reason,
                )
            self.portfolio.sell_eth(order.price, order.size_base, fee)
            self.sell_count += 1

        self.trade_count += 1

        return FillResult(
            True,
            order.side,
            order.price,
            order.size_base,
            order.size_usd,
            fee,
            "fill",
            order.execution_type,
            order.slippage_bps,
            order.trade_reason,
        )
