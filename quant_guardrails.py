"""Pure, testable portfolio and NSE long-option evaluation rules.

This module NEVER places broker orders. All amounts are INR.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

CAPITAL_LIMIT_INR = 1_000_000.0
EQUITY_TARGET_INR = 100_000.0
EQUITY_MAX_SINGLE_INR = 200_000.0
EQUITY_RISK_PER_TRADE_INR = 5_000.0
OPTION_MAX_SINGLE_INR = 50_000.0
OPTION_TOTAL_LIMIT_INR = 200_000.0
TOTAL_OPEN_EQUITY_RISK_INR = 30_000.0


def remaining_capital(equity_inr: float, options_inr: float) -> float:
    if min(equity_inr, options_inr) < 0:
        raise ValueError("Negative allocation is invalid")
    return max(0.0, CAPITAL_LIMIT_INR - equity_inr - options_inr)


def allowable_equity_shares(price: float, stop: float, equity_inr: float, options_inr: float,
                            existing_risk_inr: float = 0.0) -> int:
    """Respect total portfolio exposure, per-trade size, and remaining stop-risk budget."""
    if not all(math.isfinite(v) for v in (price, stop, equity_inr, options_inr, existing_risk_inr)):
        return 0
    if price <= 0 or not 0 <= stop < price:
        return 0
    free = remaining_capital(equity_inr, options_inr)
    risk_free = max(0.0, TOTAL_OPEN_EQUITY_RISK_INR - existing_risk_inr)
    if not risk_free:
        return 0
    budget_by_stop = min(EQUITY_RISK_PER_TRADE_INR, risk_free) / ((price - stop) / price)
    max_buy = min(free, EQUITY_MAX_SINGLE_INR, max(EQUITY_TARGET_INR, price), budget_by_stop)
    return max(0, int((max_buy + 1e-9) // price))


def can_allocate_option(cost: float, equity_inr: float, options_inr: float) -> bool:
    return (math.isfinite(cost) and cost > 0 and cost <= OPTION_MAX_SINGLE_INR
            and options_inr + cost <= OPTION_TOTAL_LIMIT_INR
            and cost <= remaining_capital(equity_inr, options_inr))


@dataclass(frozen=True)
class OptionReview:
    decision: str
    reason: str
    cost_inr: float
    max_loss_inr: float
    breakeven_spot: float
    spread_pct: float
    iv_to_realized: float | None


def review_long_option(*, kind: str, spot: float, strike: float, bid: float, ask: float,
                       lot_size: int, days_left: int, open_interest: float, iv_pct: float,
                       realized_vol_pct: float, ret_5d_pct: float, rsi: float,
                       recent_filing_risk: bool, equity_exposure: float, option_exposure: float,
                       quote_fresh: bool = True) -> OptionReview:
    """Conservative, explainable research labels. 'CONSIDER' is NOT a trade instruction.

    A missing/unusable live quote cannot pass, regardless of any theoretical valuation.
    """
    reasons = []
    if kind not in ("CE", "PE") or spot <= 0 or strike <= 0 or lot_size <= 0:
        return OptionReview("AVOID", "Invalid contract data", 0, 0, 0, 0, None)
    be = strike + ask if kind == "CE" else strike - ask
    if bid <= 0 or ask <= 0 or bid > ask or not quote_fresh:
        return OptionReview("AVOID", "Missing, crossed or stale live bid/ask", 0, 0, be, 0, None)
    spread = 100 * (ask - bid) / max(0.01, (ask + bid) / 2)
    cost = round(ask * lot_size, 2)
    ratio = iv_pct / realized_vol_pct if realized_vol_pct > 0 and iv_pct > 0 else None
    if not can_allocate_option(cost, equity_exposure, option_exposure):
        reasons.append("One lot exceeds the ₹50,000 option limit, ₹2 lakh options pool or remaining capital")
    if days_left < 8:
        reasons.append("Expiry is too close; rapid time decay/physical settlement risk")
    if open_interest < 100:
        reasons.append("Open interest is too low")
    if spread > 7:
        reasons.append(f"Bid/ask spread is wide ({spread:.1f}%)")
    if recent_filing_risk:
        reasons.append("Adverse exchange announcement requires review")
    if ratio is not None and ratio > 2.0:
        reasons.append(f"Option implied volatility is expensive versus historical volatility ({ratio:.1f}×)")
    if reasons:
        return OptionReview("AVOID", "; ".join(reasons), cost, cost, round(be, 2), round(spread, 2), round(ratio, 2) if ratio else None)
    aligns = (kind == "CE" and ret_5d_pct >= 1.2 and 48 <= rsi <= 73) or (kind == "PE" and ret_5d_pct <= -1.2 and 27 <= rsi <= 52)
    if not aligns:
        return OptionReview("WATCH", "Price trend does not convincingly support this direction", cost, cost, round(be, 2), round(spread, 2), round(ratio, 2) if ratio else None)
    return OptionReview("CONSIDER", "Live quote, usable liquidity, manageable premium, direction aligned with the recent trend; losses are still possible", cost, cost, round(be, 2), round(spread, 2), round(ratio, 2) if ratio else None)
