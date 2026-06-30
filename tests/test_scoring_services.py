from __future__ import annotations

from datetime import date, timedelta

import pytest


@pytest.mark.parametrize(
    ("age", "confidence", "bureau", "expected"),
    [
        (17, 1.0, {}, {"HS-01"}),
        (76, 1.0, {}, {"HS-02"}),
        (30, 0.69, {}, {"HS-03"}),
        (30, 1.0, {"total_judgements": "1"}, {"HS-04"}),
        (30, 1.0, {"has_written_off_within_36m": True}, {"HS-05"}),
        (30, 1.0, {"delinquency_rating": "4"}, {"HS-06"}),
        (30, 1.0, {"delinquency_rating": "Very Poor"}, {"HS-06"}),
        (30, 1.0, {"total_accounts_in_arrear": "3"}, {"HS-07"}),
    ],
)
def test_each_hard_stop_rule(age, confidence, bureau, expected) -> None:
    from app.services.hard_stops import evaluate_hard_stops

    result = evaluate_hard_stops(age, confidence, bureau)
    assert not result.passed
    assert set(result.triggered_rules) == expected
    assert len(result.decline_reasons) == len(expected)


def test_hard_stops_pass_and_accumulate() -> None:
    from app.services.hard_stops import evaluate_hard_stops

    assert evaluate_hard_stops(30, 0.9, {}).passed
    result = evaluate_hard_stops(
        12,
        0.2,
        {
            "total_judgements": 1,
            "has_written_off_within_36m": True,
            "delinquency_rating": "Very Poor",
            "total_accounts_in_arrear": 4,
        },
    )
    assert len(result.triggered_rules) == 6


def test_extract_hard_stop_signals_and_de_payload() -> None:
    from app.services.scoring_pipeline import build_de_payload, extract_hard_stop_signals

    recent = (date.today() - timedelta(days=20)).strftime("%d/%m/%Y")
    raw = {
        "product_45": {
            "highestDelinquencyRating": [{"highestDelinquencyRating": "4"}],
            "creditAccountSummary": [
                {"totalNumberofJudgementGHS": "1", "totalAccountInArrearGHS": "3"}
            ],
            "creditAgreementSummary": [{"accountStatusCode": "W", "changedOnDate": recent}],
            "personalDetailsSummary": [{"nationalIDNo": "GHA-1", "birthDate": "01/01/1990"}],
        },
        "product_49": {},
    }
    age, confidence, bureau = extract_hard_stop_signals(raw)
    assert age is not None and age >= 35
    assert confidence == 1.0
    assert bureau == {
        "delinquency_rating": "4",
        "total_judgements": 1,
        "total_accounts_in_arrear": 3,
        "has_written_off_within_36m": True,
    }
    payload = build_de_payload("SCR-1", raw)
    assert payload["request_id"] == "SCR-1"
    assert payload["xds_payload"]["consumer_full_report_45"] == raw["product_45"]


def test_extract_signals_fallbacks_and_invalid_values() -> None:
    from app.services.scoring_pipeline import extract_hard_stop_signals

    dob = date.today().replace(year=date.today().year - 40)
    age, confidence, bureau = extract_hard_stop_signals(
        {
            "product_45": {
                "creditAccountSummary": {"totalNumberofJudgementGHS": "bad"},
                "creditAgreementSummary": [{"accountStatusCode": "W", "closedDate": "bad"}],
            },
            "product_49": {
                "creditAccountSummary": {"totalAccountarrear": "2"},
                "personalDetails": {"nationalIDNo": "ALT-ID"},
            },
        },
        dob,
    )
    assert age in {39, 40}
    assert confidence == 1.0
    assert bureau["total_accounts_in_arrear"] == 2
    assert bureau["has_written_off_within_36m"] is True


def test_reverse_mapper_applies_all_override_families() -> None:
    from app.services.xds_reverse_mapper import apply_feature_overrides

    result = apply_feature_overrides(
        {"product_45": {}, "product_49": {"creditAccountSummary": {}}},
        {
            "highest_delinquency_rating": 3,
            "applicant_age": "01/01/1990",
            "num_dependants": 2,
            "has_employer_detail": "Bank",
            "identity_verified": "GHA-1",
            "total_outstanding_debt_ghs": 500,
            "total_monthly_instalment_ghs": 50,
            "num_active_accounts": 2,
            "num_accounts_total": 4,
            "has_active_arrears": 1,
            "total_arrear_amount_ghs": 10,
            "num_bounced_cheques": 1,
            "has_judgement": 1,
            "has_written_off": True,
            "has_adverse_default": True,
            "num_enquiries_12m": 2,
            "mobile_max_loan_ghs": 2000,
            "mobile_loan_history_count": 3,
        },
    )
    p45 = result["product_45"]
    assert p45["highestDelinquencyRating"]["highestDelinquencyRating"] == "3"
    assert p45["creditAgreementSummary"][0]["accountStatusCode"] == "W"
    assert len(p45["enquiryHistory"]) == 2
    assert p45["defaults"]
    assert len(result["product_49"]["detailedFacilityInfo"]) == 3


@pytest.mark.parametrize(
    ("score", "category"),
    [(800, "very_low"), (700, "low"), (600, "medium"), (500, "high"), (400, "very_high")],
)
def test_mock_score_risk_bands(score, category, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import mock_scorer

    assert mock_scorer._risk_category(score)[0] == category
    monkeypatch.setattr(mock_scorer, "_deterministic_score", lambda _: score)
    response = mock_scorer.generate_mock_score(
        "SCR-TEST",
        {
            "applicant": {"full_name": "Ada", "national_id_number": "GHA-1"},
            "loan_request": {"amount": 12000, "tenure_months": 12},
            "employment": {"monthly_income": 5000},
        },
    )
    assert response.score is not None and response.score.value == score
    assert response.recommendations


def test_mock_score_is_deterministic() -> None:
    from app.services.mock_scorer import _deterministic_score

    payload = {"applicant": {"full_name": "Ada", "national_id_number": "GHA-1"}}
    assert _deterministic_score(payload) == _deterministic_score(payload)
    assert 300 <= _deterministic_score(payload) <= 850
