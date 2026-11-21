"""Kro GeoIP independent region/price evidence evaluator.

READ-ONLY / OFFLINE. This module never contacts Midasbuy, Telegram, or any
other service; it does not switch regions or modify shopping eligibility.

Usage: python region_lab.py samples/session_specific.json
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
ALLOWED_PHASES = {"before", "after"}
ALLOWED_ELIGIBILITY = {"unknown", "allowed", "rejected"}
CHECKOUT_OUTCOMES = {"quoted", "rejected", "paid"}


@dataclass(frozen=True)
class Observation:
    phase: str
    session: str
    independently_started: bool
    shop_country: str
    currency: str
    price: Decimal | None
    eligibility: str


def _amount(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError("invalid price") from exc
    if not value.is_finite() or value < 0:
        raise ValueError("price must be a finite nonnegative number")
    return value


def _observation(raw: dict[str, Any]) -> Observation:
    if not isinstance(raw, dict):
        raise ValueError("Every observation must be a JSON object")
    phase = raw.get("phase")
    session = raw.get("session")
    country = raw.get("shop_country")
    currency = raw.get("currency")
    eligibility = raw.get("eligibility", "unknown")
    if not isinstance(phase, str) or phase not in ALLOWED_PHASES:
        raise ValueError("phase must be 'before' or 'after'")
    if not isinstance(session, str) or not SESSION_RE.fullmatch(session):
        raise ValueError("session must be a simple short ID (A, B, etc.)")
    if not isinstance(country, str) or not COUNTRY_RE.fullmatch(country):
        raise ValueError("shop_country must be a two-letter ISO code")
    if not isinstance(currency, str) or not CURRENCY_RE.fullmatch(currency):
        raise ValueError("currency must be a three-letter ISO code")
    if not isinstance(raw.get("independently_started"), bool):
        raise ValueError("independently_started must be true/false")
    if not isinstance(eligibility, str) or eligibility not in ALLOWED_ELIGIBILITY:
        raise ValueError("eligibility must be unknown, allowed, or rejected")
    return Observation(phase, session, raw["independently_started"], country,
                       currency, _amount(raw.get("price")), eligibility)


def _checked_code(value: Any, pattern: re.Pattern, field: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _account_evidence(raw: Any) -> dict[str, Any]:
    """Summarize transcribed evidence for one account and the document's SKU."""
    if not isinstance(raw, dict) or not raw or not raw.keys() <= ALLOWED_PHASES:
        raise ValueError("account_evidence requires before and/or after")
    observations = {}
    for phase, row in raw.items():
        if not isinstance(row, dict) or not row or not row.keys() <= {"region", "checkout"}:
            raise ValueError(f"account_evidence.{phase} requires region and/or checkout")
        observation = {}
        if "region" in row:
            region = row["region"]
            if not isinstance(region, dict) or region.keys() != {"user_region", "reference"}:
                raise ValueError("region requires user_region and a redacted reference label")
            observation["region"] = {
                "user_region": _checked_code(region["user_region"], COUNTRY_RE, "user_region"),
                "reference": _checked_code(region["reference"], SESSION_RE, "region reference"),
            }
        if "checkout" in row:
            checkout = row["checkout"]
            required = {"shop_country", "outcome", "payment_method", "reference"}
            if (not isinstance(checkout, dict) or not required <= checkout.keys()
                    or not checkout.keys() <= required | {"total", "currency"}):
                raise ValueError("invalid checkout fields")
            outcome = checkout["outcome"]
            if not isinstance(outcome, str) or outcome not in CHECKOUT_OUTCOMES:
                raise ValueError("checkout outcome must be quoted, rejected, or paid")
            total = _amount(checkout.get("total"))
            currency = checkout.get("currency")
            if outcome == "rejected":
                if total is not None or currency is not None:
                    raise ValueError("a rejected checkout has no comparable total")
            else:
                if total is None:
                    raise ValueError("quoted and paid checkout require a total")
                _checked_code(currency, CURRENCY_RE, "checkout currency")
            observation["checkout"] = {
                "shop_country": _checked_code(checkout["shop_country"], COUNTRY_RE, "checkout shop_country"),
                "outcome": outcome,
                "payment_method": _checked_code(checkout["payment_method"], SESSION_RE, "payment method label"),
                "reference": _checked_code(checkout["reference"], SESSION_RE, "checkout reference"),
                "total": str(total) if total is not None else None,
                "currency": currency,
            }
        observations[phase] = observation

    before = observations.get("before", {})
    after = observations.get("after", {})
    region_before = before.get("region", {}).get("user_region")
    region_after = after.get("region", {}).get("user_region")
    region_changed = None if not (region_before and region_after) else region_before != region_after
    checkout_before, checkout_after = before.get("checkout", {}), after.get("checkout", {})
    total_changed = None
    if (checkout_before.get("total") is not None and checkout_after.get("total") is not None
            and all(checkout_before[key] == checkout_after[key]
                    for key in ("outcome", "currency", "payment_method"))):
        total_changed = Decimal(checkout_before["total"]) != Decimal(checkout_after["total"])

    return {
        "source": "MANUALLY_RECORDED_UNVERIFIED",
        "observations": observations,
        "user_region_changed_as_reported": region_changed,
        "comparable_checkout_total_changed_as_reported": total_changed,
        "turkish_checkout_outcome_as_reported": (
            checkout_after["outcome"] if checkout_after.get("shop_country") == "TR" else None
        ),
    }


def evaluate(data: dict[str, Any]) -> dict[str, Any]:
    """Analyze manually recorded store observations. No mutation claim possible."""
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    sku = data.get("sku")
    if not isinstance(sku, str) or not sku.strip() or len(sku) > 80:
        raise ValueError("a single product sku is required for fair comparison")
    account = _account_evidence(data["account_evidence"]) if "account_evidence" in data else None
    records = data.get("observations", [])
    if not isinstance(records, list) or len(records) > 30 or (not records and account is None):
        raise ValueError("1 to 30 storefront observations or account_evidence required")
    observations = [_observation(x) for x in records]
    sessions = sorted({obs.session for obs in observations})
    before = [obs for obs in observations if obs.phase == "before"]
    after = [obs for obs in observations if obs.phase == "after"]
    evidence: list[str] = []
    limitations: list[str] = [
        "هذه ملاحظات يدوّنها المستخدم، وليست إثباتاً من API مستقل.",
        "توافق العملة والسعر لا يثبت تعديل user_region أو قبول الدفع.",
    ]
    if not before:
        limitations.append("لا توجد ملاحظة لواجهة المتجر قبل العملية، فلا يمكن مقارنة سعر العرض.")
    if not after:
        limitations.append("لا توجد ملاحظة لواجهة المتجر بعد العملية.")

    # A before/after comparison requires the same session; otherwise
    # observations alone cannot establish that anything changed.
    pairs = [(a, b) for a in after for b in before if a.session == b.session]
    has_comparable_pair = bool(pairs)
    if before and after and not has_comparable_pair:
        limitations.append("لا توجد مقارنة قبل وبعد في نفس الجلسة.")
    # Compare only same-session observations with the same product SKU.
    changed_original = any(
        (a.shop_country, a.currency) != (b.shop_country, b.currency)
        or different_price(a, b)
        for a, b in pairs
    )
    if changed_original:
        evidence.append("DISPLAY_OR_PRICE_CHANGED_WITHIN_SESSION")
    if any(a.currency == b.currency and (a.price is None or b.price is None) for a, b in pairs):
        limitations.append("مقارنة السعر تستلزم قيمتين مسجلتين بالعملة نفسها.")

    original_sessions = {obs.session for obs in observations if not obs.independently_started}
    independent_after = [
        obs for obs in after if obs.independently_started and obs.session not in original_sessions
    ]
    if independent_after:
        evidence.append("SEPARATE_SESSION_OBSERVATION_RECORDED")
    else:
        limitations.append("لا توجد نتيجة من جلسة جديدة مستقلة بعد العملية.")

    tr_try_after = [obs for obs in after if obs.shop_country == "TR" and obs.currency == "TRY"]
    if tr_try_after:
        evidence.append("TURKISH_STORE_AND_CURRENCY_OBSERVED")
    if any(obs.shop_country == "TR" and obs.currency != "TRY" for obs in after):
        evidence.append("TURKEY_LABEL_WITH_NON_TRY_CURRENCY")

    cross_session_pairs = [(o, i) for o in after for i in independent_after if o.session != i.session]
    price_conflict = any(different_price(o, i) for o, i in cross_session_pairs)
    independent_conflict = price_conflict or any(
        (o.shop_country, o.currency) != (i.shop_country, i.currency)
        for o, i in cross_session_pairs
    )
    if price_conflict:
        evidence.append("SAME_CURRENCY_PRICE_DIFFERS_ACROSS_SESSIONS")
    if independent_conflict:
        evidence.append("SESSION_DEPENDENT_RESULT_OBSERVED")
    elif (len({o.session for o in tr_try_after}) >= 2
          and any(o in independent_after for o in tr_try_after)
          and all((i.shop_country, i.currency) == ("TR", "TRY") for i in independent_after)):
        # Never call a single independent observation "repeated across sessions".
        evidence.append("TURKISH_DISPLAY_REPEATED_ACROSS_SESSIONS")

    if not before or not after:
        classification = "INSUFFICIENT_EVIDENCE"
    elif independent_conflict:
        classification = "SESSION_SPECIFIC_RESULT"
    elif "TURKISH_DISPLAY_REPEATED_ACROSS_SESSIONS" in evidence:
        classification = "CROSS_SESSION_DISPLAY_OBSERVED"
    elif changed_original:
        classification = "SESSION_ONLY_CHANGE_OBSERVED"
    elif not has_comparable_pair:
        classification = "INSUFFICIENT_EVIDENCE"
    else:
        classification = "NO_DEMONSTRATED_CHANGE"

    result = {
        "sku": sku,
        "sessions_observed": sessions,
        "classification": classification,
        "evidence": evidence,
        "limitations": limitations,
        "user_region_mutation_verified": False,
        "turkish_purchase_eligibility_verified": False,
        "operation_performed": False,
    }
    if account is not None:
        result["account_evidence"] = account
    return result


def different_price(first: Observation, second: Observation) -> bool:
    return (
        first.currency == second.currency and first.price is not None
        and second.price is not None and first.price != second.price
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Midasbuy region evidence evaluation")
    parser.add_argument("file", type=Path, help="JSON observations; no passwords/cookies")
    args = parser.parse_args()
    try:
        data = json.loads(args.file.read_text(encoding="utf-8"), parse_float=Decimal)
        result = evaluate(data)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
