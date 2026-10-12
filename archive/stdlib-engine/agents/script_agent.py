"""Script agent — builds the per-tier video script for each classified lead.

Tag: ``[Agent-Script]``. Status move: ``classified`` -> ``scripted``.

Every generated script is persisted to ``<DATA_DIR>/scripts/<lead_id>.json``
before the row advances, so the owner can read (and approve) exactly what will
be said before any render is queued.

TRUTH RULES (hard, do not relax)
--------------------------------
1. Never invent a metric, result, testimonial, client name, award or deadline.
   The templates below only ever state things the engine actually knows: the
   company name, the website and the industry that came in on the lead row.
2. Never claim a price unless the matching ``TIER_PRICE_*`` env var is
   configured. All three are unset by default and the pricing line is omitted
   entirely when unset — no pricing exists in Stripe for this business yet, so
   the copy must not imply one.
3. Keep an honest, easy opt-out in the copy. No manufactured urgency, no
   "final notice", no fake scarcity.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from agents.base import Agent
from db import (
    STATUS_CLASSIFIED,
    STATUS_FAILED,
    STATUS_SCRIPTED,
    TIERS,
    Database,
    utcnow,
)

# ===========================================================================
# TIER SCRIPT TEMPLATES — the one place to tune the copy.
# ---------------------------------------------------------------------------
# Placeholders resolved per lead (all always defined, so str.format is safe):
#   {company}            company name, or "your business" when unknown
#   {for_company_phrase} "for Alpha Shop" / "for you"
#   {website_sentence}   "We had a look at alpha.com." / "" when unknown
#   {industry_sentence}  "You're listed in Retail." / "" when unknown
#   {price_sentence}     pricing line, "" when no TIER_PRICE_* is configured
#   {tier}               the tier name itself
# Any line that comes out empty is dropped, so unknown fields leave no gaps.
# ===========================================================================

TIER_TEMPLATES = {
    # ~15-20s. Short, punchy hook.
    "quick": {
        "target_duration_seconds": 18,
        "lines": (
            "Hi — here's the short version, about twenty seconds.",
            "We put this personalised video together {for_company_phrase}.",
            "{website_sentence}",
            "{industry_sentence}",
            "If it looks useful, reply to the email this came with and we'll send the full cut.",
            "Not interested? The opt-out link is at the bottom of that same email — one click and you won't hear from us again.",
            "{price_sentence}",
        ),
    },
    # ~30-45s. Led by the outcome / value for the business.
    "scale": {
        "target_duration_seconds": 38,
        "lines": (
            "Hi — this is a short, personalised video {for_company_phrase}.",
            "{website_sentence}",
            "{industry_sentence}",
            "Here's what we do: we produce short videos, one per business, built around what that business actually does — no stock footage and no generic script.",
            "For {company} that means a video that speaks to your own work, instead of another templated email nobody finishes reading.",
            "There is nothing to buy from this video — it is an introduction. If it is relevant, reply to the email it came with and we'll take it from there.",
            "You can opt out with the link in that same email.",
            "{price_sentence}",
        ),
    },
    # ~45-60s. Partnership / white-label framing.
    "whitelabel": {
        "target_duration_seconds": 52,
        "lines": (
            "Hi — a short introduction {for_company_phrase}.",
            "{website_sentence}",
            "{industry_sentence}",
            "We produce personalised video at volume, and the white-label side is probably the part that fits you: videos delivered under your brand rather than ours.",
            "Your client sees your name and your styling on every video — our name does not appear on it.",
            "Each video is generated from the details of the business it is for, and every script is reviewed before a render is queued.",
            "If you want the details — volumes, turnaround and how the branding works — reply to the email this came with.",
            "The opt-out link is in that same email if it isn't for you.",
            "{price_sentence}",
        ),
    },
}

#: Rough speaking rate used only to report an estimated length to the owner.
WORDS_PER_SECOND = 2.5


@dataclass
class Script:
    """A generated script for one lead."""

    lead_id: int
    email: str
    tier: str
    text: str
    target_duration_seconds: int
    word_count: int
    created_at: str
    estimated_seconds: int

    def as_dict(self) -> dict:
        """The exact JSON persisted to ``data/scripts/<lead_id>.json``."""
        return {
            "lead_id": self.lead_id,
            "email": self.email,
            "tier": self.tier,
            "script": self.text,
            "target_duration_seconds": self.target_duration_seconds,
            "word_count": self.word_count,
            "estimated_seconds": self.estimated_seconds,
            "created_at": self.created_at,
        }


def build_context(
    *,
    company: str | None = None,
    website: str | None = None,
    industry: str | None = None,
    price: str | None = None,
    tier: str = "",
) -> dict[str, str]:
    """Resolve the template placeholders for one lead (truthfully)."""
    company = (company or "").strip()
    website = (website or "").strip()
    industry = (industry or "").strip()
    price = (price or "").strip()
    return {
        "tier": tier,
        "company": company or "your business",
        "for_company_phrase": f"for {company}" if company else "for you",
        "website_sentence": f"We had a look at {website}." if website else "",
        "industry_sentence": f"You're listed in {industry}." if industry else "",
        # Only ever present when the owner has configured a real price.
        "price_sentence": f"Pricing for this tier is {price}." if price else "",
    }


def build_script(
    *,
    lead_id: int,
    email: str,
    tier: str,
    company: str | None = None,
    website: str | None = None,
    industry: str | None = None,
    price: str | None = None,
    created_at: str | None = None,
) -> Script:
    """Render one tier template into a finished script.

    Raises ``KeyError`` for an unknown tier — the caller turns that into a
    ``failed`` row with the reason logged, rather than silently picking a tier.
    """
    template = TIER_TEMPLATES[tier]
    context = build_context(
        company=company, website=website, industry=industry, price=price, tier=tier
    )
    lines = []
    for line in template["lines"]:
        rendered = line.format(**context).strip()
        if rendered:
            lines.append(rendered)
    text = " ".join(lines)
    words = len(text.split())
    return Script(
        lead_id=lead_id,
        email=email,
        tier=tier,
        text=text,
        target_duration_seconds=int(template["target_duration_seconds"]),
        word_count=words,
        created_at=created_at or utcnow(),
        estimated_seconds=max(1, round(words / WORDS_PER_SECOND)),
    )


class ScriptAgent(Agent):
    """Turns every ``classified`` lead into a persisted, reviewable script."""

    name = "Script"
    tag = "[Agent-Script]"

    def __init__(self, config=None, db: Database | None = None) -> None:
        super().__init__(config)
        self.db = db or Database(getattr(config, "db_path", "./leads.db"))

    # -- paths ------------------------------------------------------------
    @property
    def scripts_dir(self) -> Path:
        return Path(getattr(self.config, "scripts_dir", "./data/scripts"))

    def script_path(self, lead_id: int) -> Path:
        return self.scripts_dir / f"{lead_id}.json"

    def _tier_price(self, tier: str) -> str:
        prices = getattr(self.config, "tier_prices", {}) or {}
        return (prices.get(tier) or "").strip()

    # -- persistence ------------------------------------------------------
    def persist(self, script: Script) -> Path:
        """Write the script JSON atomically so a crash cannot truncate it."""
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        target = self.script_path(script.lead_id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(script.as_dict(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)
        return target

    # -- stage ------------------------------------------------------------
    def handle(self, item: dict):
        """Build a script for one lead dict (used by the base run() loop)."""
        return build_script(**item)

    def run_once(self) -> dict[str, int]:
        """Script every ``classified`` lead; ``classified`` -> ``scripted``."""
        totals = {"scripted": 0, "failed": 0}
        with self.db.connect() as conn:
            rows = self.db.leads_by_status(STATUS_CLASSIFIED, conn)
            if not rows:
                self.log("no 'classified' leads to script")
                return totals
            for row in rows:
                lead_id = row["id"]
                tier = (row["tier"] or "").strip()
                try:
                    if tier not in TIER_TEMPLATES:
                        raise ValueError(f"unknown tier {tier!r}")
                    firm = self.db.firmographics(conn, lead_id)
                    script = build_script(
                        lead_id=lead_id,
                        email=row["email"],
                        tier=tier,
                        company=firm.get("company"),
                        website=row["website"],
                        industry=firm.get("industry"),
                        price=self._tier_price(tier),
                    )
                    path = self.persist(script)
                    self.db.update_lead(
                        lead_id, conn=conn, status=STATUS_SCRIPTED
                    )
                    totals["scripted"] += 1
                    self.log(
                        f"lead {lead_id} tier={tier} scripted "
                        f"({script.word_count} words, ~{script.estimated_seconds}s, "
                        f"target {script.target_duration_seconds}s) -> {path.name} "
                        f"[status={STATUS_SCRIPTED}]"
                    )
                except Exception as exc:  # noqa: BLE001 - per-lead safety net
                    self.log_error(
                        f"lead {lead_id} script failed ({type(exc).__name__}: {exc})"
                    )
                    self.db.update_lead(lead_id, conn=conn, status=STATUS_FAILED)
                    totals["failed"] += 1
            conn.commit()
        self.log(
            f"scripting complete: {totals['scripted']} scripted, "
            f"{totals['failed']} failed (tiers: {', '.join(TIERS)})"
        )
        return totals

    # -- reads for the video stage ---------------------------------------
    def load(self, lead_id: int) -> dict | None:
        """Read a previously persisted script, or None when absent."""
        path = self.script_path(lead_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.log_error(f"cannot read script for lead {lead_id}: {exc}")
            return None
