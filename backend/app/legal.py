"""Legal settings and rules shared by sign-up, the account page and the legal pages.

The privacy policy and terms of use shown in the dashboard are a TEMPLATE written for a student research
project. They are not legal advice. Fill in the operator details with these environment variables and have
the text checked (for example by your university's privacy or legal office) before opening sign-up to the public:

    LEGAL_OPERATOR_NAME     who runs the dashboard and decides how personal data is used (the data controller)
    LEGAL_CONTACT_EMAIL     where people send privacy questions, data requests and complaints
    LEGAL_JURISDICTION      whose law the terms follow, e.g. "Queensland, Australia" or "England and Wales"
    AUDIT_RETENTION_DAYS    how long audit log rows are kept (default 365)
    INACTIVE_ACCOUNT_DAYS   accounts with no sign-in for this long are deleted (default 730; 0 turns it off)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

import app.config  # noqa: F401  (loads backend/.env before the variables below are read)

# Change this date whenever the privacy policy or terms change in a way users must agree to again.
# Everyone whose accepted version is older is asked to read and accept the new one at their next sign-in.
TERMS_VERSION = "2026-11-14"

# 18 because the dashboard deals with financial crime (sanctions, ransomware, scams) and asks users to accept
# binding terms; adults can do that themselves in every likely jurisdiction (UK, EU, Australia), and the tool is
# not designed for children. It also keeps the service clear of the UK Age Appropriate Design Code.
MIN_AGE = 18

USER_TYPES = {
    "researcher": "Researcher",
    "student": "Student",
    "organisation": "Organisation or company",
    "law_enforcement": "Law enforcement or government",
    "other": "Other",
}
# Types that are asked for the name of their organisation (optional).
ORGANISATION_TYPES = ("organisation", "law_enforcement", "researcher")


@dataclass(frozen=True)
class LegalSettings:
    operator_name: str
    contact_email: str
    jurisdiction: str
    audit_retention_days: int
    inactive_account_days: int

    @property
    def configured(self) -> bool:
        return bool(self.operator_name and self.contact_email)

    def public(self) -> dict:
        return {
            "operator_name": self.operator_name,
            "contact_email": self.contact_email,
            "jurisdiction": self.jurisdiction,
            "configured": self.configured,
            "terms_version": TERMS_VERSION,
            "min_age": MIN_AGE,
            "user_types": USER_TYPES,
            "organisation_types": list(ORGANISATION_TYPES),
            "retention": {
                "audit_days": self.audit_retention_days,
                "inactive_account_days": self.inactive_account_days,
                "session_hours": float(os.getenv("SESSION_HOURS", "168")),
            },
        }


def _int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


def legal_settings() -> LegalSettings:
    return LegalSettings(
        operator_name=os.getenv("LEGAL_OPERATOR_NAME", "").strip(),
        contact_email=os.getenv("LEGAL_CONTACT_EMAIL", "").strip(),
        jurisdiction=os.getenv("LEGAL_JURISDICTION", "").strip(),
        audit_retention_days=_int("AUDIT_RETENTION_DAYS", 365),
        inactive_account_days=_int("INACTIVE_ACCOUNT_DAYS", 730),
    )


def age_on(birth: date, today: date) -> int:
    """Whole years between a date of birth and today (a birthday on 29 February counts from 1 March)."""
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def age_problem(birth: date | None, today: date | None = None) -> str | None:
    """Why this date of birth cannot sign up, or None. The date itself is never stored."""
    today = today or date.today()
    if birth is None:
        return "Please enter your date of birth."
    if birth > today:
        return "Your date of birth cannot be in the future."
    age = age_on(birth, today)
    if age > 120:
        return "Please check your date of birth."
    if age < MIN_AGE:
        return f"You must be at least {MIN_AGE} to use this dashboard."
    return None


def user_type_problem(user_type: str | None) -> str | None:
    return None if user_type in USER_TYPES else "Please choose what describes you best."


def clean_organisation(user_type: str, organisation: str | None) -> str | None:
    value = (organisation or "").strip()[:120]
    return value if value and user_type in ORGANISATION_TYPES else None
