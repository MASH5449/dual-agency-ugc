"""Classifier agent — firmographic tiering for LeadLoop (stdlib only).

Assigns exactly one tier to each lead: ``quick`` / ``scale`` / ``whitelabel``.

TUNING: every threshold and keyword lives in the block below — nothing is
hard-coded anywhere else in the file. Change them here.

Decision order (deterministic, first match wins):

1. An explicit ``tier`` on the input always wins (must be a known tier).
2. ``whitelabel`` — the agency/reseller shape: an agency/reseller/white-label
   keyword in the company name or industry, OR employees above
   ``SCALE_MAX_EMPLOYEES``, OR annual revenue above ``SCALE_MAX_REVENUE``.
3. ``quick`` — the small self-serve shape: employees at or below
   ``QUICK_MAX_EMPLOYEES`` AND revenue at or below ``QUICK_MAX_REVENUE``.
4. ``scale`` — everything in between.

Status lifecycle handled here: ``new`` -> ``classified``. A lead that cannot
be classified is marked ``failed`` with the reason logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agents.base import Agent
from db import (
    Database,
    STATUS_CLASSIFIED,
    STATUS_FAILED,
    TIERS,
    utcnow,
)

# ---------------------------------------------------------------------------
# THRESHOLDS — single place to tune tiering
# ---------------------------------------------------------------------------

#: Employees at or below this (with revenue also at/below QUICK_MAX_REVENUE)
#: => quick.
QUICK_MAX_EMPLOYEES = 19
#: Annual revenue in whole currency units at or below this => quick.
QUICK_MAX_REVENUE = 1_000_000

#: Above either of these => whitelabel.
SCALE_MAX_EMPLOYEES = 199
SCALE_MAX_REVENUE = 25_000_000

#: Company-name / industry keywords that imply an agency-reseller shape.
WHITELABEL_KEYWORDS = (
    "agency",
    "agencies",
    "white label",
    "white-label",
    "whitelabel",
    "reseller",
    "partner network",
    "media buying",
    "performance marketing",
    "affiliate network",
)

#: Company-name / industry keywords that imply a small self-serve brand.
QUICK_KEYWORDS = (
    "boutique",
    "shop",
    "store",
    "handmade",
    "local",
    "startup",
    "sole trader",
)

#: Industries (substring match, lowercased) treated as whitelabel-shaped.
WHITELABEL_INDUSTRIES = (
    "agency",
    "advertising",
    "marketing services",
    "media",
)

# ---------------------------------------------------------------------------

_TIER_ALIASES = {
    "quick": "quick",
    "scale": "scale",
    "whitelabel": "whitelabel",
    "white label": "whitelabel",
    "white-label": "whitelabel",
    "white_label": "whitelabel",
}


@dataclass
class Classification:
    """Result of classifying one lead."""

    tier: str
    reason: str


def _to_number(value) -> float | None:
    """Best-effort numeric parse of employees/revenue fields."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if not text:
        return None
    multiplier = 1.0
    if text.endswith("k"):
        multiplier, text = 1_000.0, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000.0, text[:-1]
    elif text.endswith("b"):
        multiplier, text = 1_000_000_000.0, text[:-1]
    text = text.replace(",", "").replace("$", "").replace("£", "").replace("€", "")
    text = re.sub(r"\s*(employees?|usd|gbp|eur|revenue)\s*$", "", text).strip()
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _has_keyword(haystack: str, keywords) -> str | None:
    for keyword in keywords:
        if keyword in haystack:
            return keyword
    return None


def classify_lead(item: dict) -> Classification:
    """Return the tier and a human-readable reason for one lead record."""
    # 1. Explicit override always wins.
    explicit = item.get("tier")
    if explicit is not None and str(explicit).strip():
        alias = _TIER_ALIASES.get(str(explicit).strip().lower())
        if alias:
            return Classification(alias, f"explicit tier override: {alias}")
        return Classification("", f"explicit tier unknown: {explicit!r}")

    company = str(item.get("company") or "").strip().lower()
    industry = str(item.get("industry") or "").strip().lower()
    haystack = f"{company} {industry}"
    employees = _to_number(item.get("employees"))
    revenue = _to_number(item.get("revenue"))

    # 2. Whitelabel: agency/reseller shape or above the scale ceiling.
    keyword = _has_keyword(haystack, WHITELABEL_KEYWORDS)
    if keyword:
        return Classification("whitelabel", f"whitelabel keyword matched: {keyword!r}")
    industry_hit = _has_keyword(industry, WHITELABEL_INDUSTRIES)
    if industry_hit:
        return Classification(
            "whitelabel", f"agency-shaped industry matched: {industry_hit!r}"
        )
    if employees is not None and employees > SCALE_MAX_EMPLOYEES:
        return Classification(
            "whitelabel",
            f"employees {employees:g} > SCALE_MAX_EMPLOYEES {SCALE_MAX_EMPLOYEES}",
        )
    if revenue is not None and revenue > SCALE_MAX_REVENUE:
        return Classification(
            "whitelabel",
            f"revenue {revenue:g} > SCALE_MAX_REVENUE {SCALE_MAX_REVENUE}",
        )

    # 3. Quick: small on both axes, or a small-brand keyword.
    small_employees = employees is not None and employees <= QUICK_MAX_EMPLOYEES
    small_revenue = revenue is not None and revenue <= QUICK_MAX_REVENUE
    if small_employees and small_revenue:
        return Classification(
            "quick",
            f"small firmographics (employees {employees:g} <= "
            f"{QUICK_MAX_EMPLOYEES}, revenue {revenue:g} <= {QUICK_MAX_REVENUE})",
        )
    quick_keyword = _has_keyword(haystack, QUICK_KEYWORDS)
    if quick_keyword:
        return Classification("quick", f"quick keyword matched: {quick_keyword!r}")

    # 4. Everything else.
    detail = []
    if employees is None:
        detail.append("employees unknown")
    if revenue is None:
        detail.append("revenue unknown")
    suffix = f" ({', '.join(detail)})" if detail else ""
    return Classification("scale", f"mid-market default{suffix}")


class ClassifierAgent(Agent):
    """Moves leads from ``new`` to ``classified`` with a tier assigned."""

    name = "Classifier"
    tag = "[Agent-Classifier]"

    def __init__(self, config=None, db: Database | None = None) -> None:
        super().__init__(config)
        self.db = db or Database(getattr(config, "db_path", "./leads.db"))

    def handle(self, item: dict) -> Classification | None:
        """Classify one record (no DB write) — used by the base run() loop."""
        return classify_lead(item)

    def run_once(self) -> dict[str, int]:
        """Classify every ``new`` lead, persisting tier + status."""
        totals = {"classified": 0, "failed": 0}
        with self.db.connect() as conn:
            rows = self.db.leads_by_status("new", conn)
            if not rows:
                self.log("no 'new' leads to classify")
                return totals

            for row in rows:
                lead_id = row["id"]
                try:
                    item = {
                        "email": row["email"],
                        "website": row["website"],
                        "company": None,
                        "employees": None,
                        "revenue": None,
                        "industry": None,
                    }
                    # Firmographics sidecar: stored on the source row, joined
                    # back here so classification stays deterministic.
                    item.update(self._firmographics(conn, lead_id))

                    # A tier supplied on the input was written to leads.tier by
                    # the scraper; honour it as the explicit override.
                    existing = (row["tier"] or "").strip()
                    if existing:
                        item["tier"] = existing

                    result = classify_lead(item)
                    if result.tier not in TIERS:
                        self._mark(conn, lead_id, STATUS_FAILED, None)
                        self.log_error(
                            f"lead {lead_id} unclassifiable: {result.reason}"
                        )
                        totals["failed"] += 1
                        continue

                    self._mark(conn, lead_id, STATUS_CLASSIFIED, result.tier)
                    totals["classified"] += 1
                    self.log(
                        f"lead {lead_id} -> tier={result.tier} "
                        f"status={STATUS_CLASSIFIED} ({result.reason})"
                    )
                except Exception as exc:  # noqa: BLE001 - per-lead safety net
                    self.log_error(f"lead {lead_id} failed ({type(exc).__name__}: {exc})")
                    self._mark(conn, lead_id, STATUS_FAILED, None)
                    totals["failed"] += 1
            conn.commit()

        self.log(
            f"classification complete: {totals['classified']} classified, "
            f"{totals['failed']} failed"
        )
        return totals

    def _firmographics(self, conn, lead_id: int) -> dict:
        """Read the firmographic inputs recorded by the scraper."""
        return self.db.firmographics(conn, lead_id)

    def _mark(self, conn, lead_id: int, status: str, tier: str | None) -> None:
        if tier is None:
            conn.execute(
                "UPDATE leads SET status = ? WHERE id = ?;", (status, lead_id)
            )
        else:
            conn.execute(
                "UPDATE leads SET status = ?, tier = ? WHERE id = ?;",
                (status, tier, lead_id),
            )
