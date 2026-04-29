from src.portfolio import Portfolio


def test_portfolio_inventory_alias_and_equity() -> None:
    portfolio = Portfolio(250, 0.1)

    assert portfolio.inventory_usd(2500) == 250
    assert portfolio.inventory_value_usd(2500) == 250
    assert portfolio.total_equity_usd(2500) == 500
