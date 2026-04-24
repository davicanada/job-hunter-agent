"""Prefilter unit tests — pure Python, no LLM or network.

Tightened 2026-04-24: every title at or above mid (senior, sr, lead, manager,
mid-level, intermediate) is a hard reject. The Block 3.5 soft-flag path is
gone — nothing ambiguous survives the prefilter anymore.
"""
from __future__ import annotations

from src.models.job import Job
from src.scoring.prefilter import PrefilterResult, prefilter_job


def _job(**overrides) -> Job:
    base = {
        "external_id": "test-123",
        "source": "remoteok",
        "title": "Data Analyst",
        "company": "Test Co",
        "description": "Looking for a data analyst with SQL and Python.",
        "url": "https://example.com/job/123",
        "allows_canada": True,
    }
    base.update(overrides)
    return Job(**base)


# ---------------------------------------------------------------------------
# Passing cases
# ---------------------------------------------------------------------------
def test_prefilter_accepts_junior():
    pf = prefilter_job(_job(title="Junior Data Analyst"), profile={})
    assert pf.should_score is True
    assert pf.skip_reason is None
    assert pf.seniority_hint is None


def test_prefilter_accepts_no_seniority():
    pf = prefilter_job(_job(title="Data Analyst"), profile={})
    assert pf.should_score is True
    assert pf.skip_reason is None
    assert pf.seniority_hint is None


def test_prefilter_accepts_entry_level():
    pf = prefilter_job(_job(title="Entry Level Data Analyst"), profile={})
    assert pf.should_score is True


def test_prefilter_accepts_intern():
    pf = prefilter_job(_job(title="Data Analyst Intern"), profile={})
    assert pf.should_score is True


def test_prefilter_allows_canada_null_passes():
    pf = prefilter_job(_job(allows_canada=None), profile={})
    assert pf.should_score is True


def test_prefilter_5_years_soft():
    # 5+ years still passes; only 7+ in title hard-rejects. Scorer rubric
    # caps these at 65.
    pf = prefilter_job(_job(title="Data Analyst - 5+ years"), profile={})
    assert pf.should_score is True


def test_prefilter_word_boundary_avoids_false_positives():
    # "seniority" / "leadership" / "leading" / "managerial" must not trigger.
    pf = prefilter_job(
        _job(title="Data Analyst", description="Participate in our leadership development program and gain seniority through mentorship."),
        profile={},
    )
    assert pf.should_score is True


# ---------------------------------------------------------------------------
# Hard rejects — seniority
# ---------------------------------------------------------------------------
def test_prefilter_rejects_senior():
    pf = prefilter_job(_job(title="Senior Data Analyst"), profile={})
    assert pf.should_score is False
    assert "senior" in (pf.skip_reason or "").lower()


def test_prefilter_rejects_sr():
    pf = prefilter_job(_job(title="Sr. Data Analyst"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_sr_no_period():
    pf = prefilter_job(_job(title="Sr Data Analyst"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_tech_lead():
    pf = prefilter_job(_job(title="Tech Lead, Data"), profile={})
    assert pf.should_score is False
    assert "lead" in (pf.skip_reason or "").lower()


def test_prefilter_rejects_team_lead():
    pf = prefilter_job(_job(title="Team Lead, Analytics"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_lead_engineer():
    pf = prefilter_job(_job(title="Lead Engineer"), profile={})
    assert pf.should_score is False
    assert "lead" in (pf.skip_reason or "").lower()


def test_prefilter_rejects_lead_developer():
    pf = prefilter_job(_job(title="Lead Developer, Data"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_lead_data_scientist():
    pf = prefilter_job(_job(title="Lead Data Scientist"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_manager():
    pf = prefilter_job(_job(title="Engineering Manager"), profile={})
    assert pf.should_score is False
    assert "manager" in (pf.skip_reason or "").lower()


def test_prefilter_rejects_manager_with_qualifier():
    pf = prefilter_job(_job(title="Performance Marketing Manager"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_mid_level():
    pf = prefilter_job(_job(title="Mid-Level Data Analyst"), profile={})
    assert pf.should_score is False
    assert "mid" in (pf.skip_reason or "").lower()


def test_prefilter_rejects_mid_level_space():
    pf = prefilter_job(_job(title="Mid Level Developer"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_intermediate():
    pf = prefilter_job(_job(title="Intermediate Software Engineer"), profile={})
    assert pf.should_score is False
    assert "intermediate" in (pf.skip_reason or "").lower()


def test_prefilter_still_rejects_director():
    pf = prefilter_job(_job(title="Director of Data"), profile={})
    assert pf.should_score is False
    assert "director" in (pf.skip_reason or "").lower()


def test_prefilter_still_rejects_staff():
    pf = prefilter_job(_job(title="Staff Software Engineer"), profile={})
    assert pf.should_score is False
    assert "staff" in (pf.skip_reason or "").lower()


def test_prefilter_still_rejects_principal():
    pf = prefilter_job(_job(title="Principal Data Engineer"), profile={})
    assert pf.should_score is False
    assert "principal" in (pf.skip_reason or "").lower()


# ---------------------------------------------------------------------------
# Hard rejects — years / geography / stack / domain
# ---------------------------------------------------------------------------
def test_prefilter_8_years_reject():
    pf = prefilter_job(_job(title="Data Analyst - 8 years experience"), profile={})
    assert pf.should_score is False
    assert "years" in (pf.skip_reason or "").lower()


def test_prefilter_10_years_reject():
    pf = prefilter_job(_job(title="Data Engineer (10+ yrs)"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_us_only():
    pf = prefilter_job(_job(allows_canada=False), profile={})
    assert pf.should_score is False
    assert "canada" in (pf.skip_reason or "").lower()


def test_prefilter_rejects_mobile():
    pf = prefilter_job(_job(title="React Native Developer"), profile={})
    assert pf.should_score is False
    assert "react native" in (pf.skip_reason or "").lower()


def test_prefilter_rejects_embedded():
    pf = prefilter_job(_job(title="Embedded Firmware Engineer"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_domain_blocklist():
    pf = prefilter_job(
        _job(
            title="Operations Analyst",
            description="Work closely with our customer success team.",
        ),
        profile={},
    )
    assert pf.should_score is False
    assert "customer success" in (pf.skip_reason or "").lower()


def test_prefilter_rejects_business_development():
    pf = prefilter_job(_job(title="Business Development Representative"), profile={})
    assert pf.should_score is False
    assert "business development" in (pf.skip_reason or "").lower()


def test_prefilter_rejects_medical_coder():
    pf = prefilter_job(_job(title="Certified Medical Coder"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_crypto_trader():
    pf = prefilter_job(_job(title="Crypto Trader"), profile={})
    assert pf.should_score is False


def test_prefilter_rejects_learning_and_development():
    pf = prefilter_job(_job(title="Learning and Development Specialist"), profile={})
    assert pf.should_score is False


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------
def test_prefilter_result_shape():
    pf = prefilter_job(_job(), profile={})
    assert isinstance(pf, PrefilterResult)
    assert pf.notes == []
    assert pf.seniority_hint is None
