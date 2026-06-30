"""Reverse-map raw feature values back into XDS report structure.

Features are stored as raw human-readable values (age in years, debt in GHS,
binary 1/0 flags).  This mapper patches the cached XDS report with any
frontend-overridden feature values before sending to the DE pipeline and
before evaluating hard-stop rules.
"""

import copy
from typing import Any, cast


def _get_or_create(d: dict[str, Any], *keys: str) -> dict[str, Any]:
    curr = d
    for k in keys:
        if k not in curr or not isinstance(curr[k], dict):
            curr[k] = {}
        curr = cast(dict[str, Any], curr[k])
    return curr


def apply_feature_overrides(raw_xds: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    """Patch the raw XDS JSON with frontend-overridden features before sending to DE."""
    out = copy.deepcopy(raw_xds)

    p45 = out.get("product_45") or {}
    p49 = out.get("product_49") or {}

    def _first_or_empty(arr_key: str) -> dict[str, Any]:
        arr = p45.get(arr_key)
        if isinstance(arr, list) and arr:
            if not isinstance(arr[0], dict):
                arr[0] = {}
            return cast(dict[str, Any], arr[0])
        else:
            new_obj: dict[str, Any] = {}
            p45[arr_key] = [new_obj]
            return new_obj

    # 1. highest_delinquency_rating (raw 0–4 integer)
    if (
        "highest_delinquency_rating" in features
        and features["highest_delinquency_rating"] is not None
    ):
        del_obj = _get_or_create(p45, "highestDelinquencyRating")
        del_obj["highestDelinquencyRating"] = str(features["highest_delinquency_rating"])

    # 2. applicant_age → raw birthDate string, just insert back
    if "applicant_age" in features and features["applicant_age"] is not None:
        pd_obj = _get_or_create(p45, "personalDetailsSummary")
        pd_obj["birthDate"] = str(features["applicant_age"])

    # 3. num_dependants (raw string count)
    if "num_dependants" in features and features["num_dependants"] is not None:
        pd_obj = _get_or_create(p45, "personalDetailsSummary")
        pd_obj["dependants"] = str(features["num_dependants"])

    # 4. has_employer_detail (raw employer string)
    if "has_employer_detail" in features:
        pd_obj = _get_or_create(p45, "personalDetailsSummary")
        pd_obj["employerDetail"] = (
            str(features["has_employer_detail"]) if features["has_employer_detail"] else ""
        )

    # 5. identity_verified (raw nationalIDNo string)
    if "identity_verified" in features:
        pd_obj = _get_or_create(p45, "personalDetailsSummary")
        pd_obj["nationalIDNo"] = (
            str(features["identity_verified"]) if features["identity_verified"] else ""
        )

    # 6. Credit Account Summary — raw GHS amounts and counts
    ca_obj = _get_or_create(p45, "creditAccountSummary")

    if (
        "total_outstanding_debt_ghs" in features
        and features["total_outstanding_debt_ghs"] is not None
    ):
        ca_obj["totalOutstandingdebtGHS"] = str(features["total_outstanding_debt_ghs"])

    if (
        "total_monthly_instalment_ghs" in features
        and features["total_monthly_instalment_ghs"] is not None
    ):
        ca_obj["totalMonthlyInstalmentGHS"] = str(features["total_monthly_instalment_ghs"])

    if "num_active_accounts" in features and features["num_active_accounts"] is not None:
        ca_obj["totalActiveAccountsGHS"] = str(features["num_active_accounts"])

    if "num_accounts_total" in features and features["num_accounts_total"] is not None:
        ca_obj["totalNumberofAccountsGHS"] = str(features["num_accounts_total"])

    if "has_active_arrears" in features and features["has_active_arrears"] is not None:
        ca_obj["totalAccountInArrearGHS"] = str(features["has_active_arrears"])

    if "total_arrear_amount_ghs" in features and features["total_arrear_amount_ghs"] is not None:
        ca_obj["totalAmountInArrearGHS"] = str(features["total_arrear_amount_ghs"])

    if "num_bounced_cheques" in features and features["num_bounced_cheques"] is not None:
        ca_obj["totalNumberofDishonouredGHS"] = str(features["num_bounced_cheques"])

    if "has_judgement" in features and features["has_judgement"] is not None:
        ca_obj["totalNumberofJudgementGHS"] = str(features["has_judgement"])

    # Account status codes (bool presence checks)
    if (
        "has_written_off" in features
        or "has_charged_off" in features
        or "has_legal_handover" in features
    ):
        agr = _first_or_empty("creditAgreementSummary")
        if features.get("has_written_off"):
            agr["accountStatusCode"] = "W"
        elif features.get("has_charged_off"):
            agr["accountStatusCode"] = "G"
        elif features.get("has_legal_handover"):
            agr["accountStatusCode"] = "L"

    # has_adverse_default (bool presence check)
    if "has_adverse_default" in features:
        if features["has_adverse_default"]:
            if not p45.get("defaults"):
                p45["defaults"] = [{"defaultType": "OVERRIDDEN"}]
        else:
            p45["defaults"] = []
            p45["adverseDetails"] = []

    # enquiries (raw count string)
    if "num_enquiries_12m" in features and features["num_enquiries_12m"] is not None:
        count = int(features["num_enquiries_12m"])
        p45["enquiryHistory"] = [
            {"dateRequested": "01/01/2026 12:00:00", "enquiryReason": "OVERRIDDEN"}
            for _ in range(count)
        ]

    # Mobile Product 49
    if p49:
        ca49_obj = _get_or_create(p49, "creditAccountSummary")
        if "mobile_max_loan_ghs" in features and features["mobile_max_loan_ghs"] is not None:
            ca49_obj["highestAmountTaken"] = str(features["mobile_max_loan_ghs"])

        if (
            "mobile_loan_history_count" in features
            and features["mobile_loan_history_count"] is not None
        ):
            count = int(features["mobile_loan_history_count"])
            p49["detailedFacilityInfo"] = [{"accountStatusCode": "C"} for _ in range(count)]

    out["product_45"] = p45
    out["product_49"] = p49
    return out
