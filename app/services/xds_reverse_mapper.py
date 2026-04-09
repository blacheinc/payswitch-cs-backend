import copy
from datetime import date
from typing import Any

def _get_or_create(d: dict, *keys: str) -> dict:
    curr = d
    for k in keys:
        if k not in curr or not isinstance(curr[k], dict):
            curr[k] = {}
        curr = curr[k]
    return curr

def apply_feature_overrides(raw_xds: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    """Patch the raw XDS JSON with frontend-overridden features before sending to DE."""
    # Deep copy to avoid mutating cache in-place if passed by reference
    out = copy.deepcopy(raw_xds)
    
    p45 = out.get("product_45") or {}
    p49 = out.get("product_49") or {}
    
    # helper for p45 array elements
    def _first_or_empty(arr_key: str) -> dict:
        arr = p45.get(arr_key)
        if isinstance(arr, list) and arr:
            if not isinstance(arr[0], dict):
                arr[0] = {}
            return arr[0]
        else:
            new_obj = {}
            p45[arr_key] = [new_obj]
            return new_obj

    # 1. highest_delinquency_rating
    if "highest_delinquency_rating" in features:
        del_obj = _get_or_create(p45, "highestDelinquencyRating")
        del_obj["highestDelinquencyRating"] = str(features["highest_delinquency_rating"])
        
    # 2. applicant_age -> reverse to birthDate (approximate year only)
    if "applicant_age" in features and features["applicant_age"]:
        age = int(float(features["applicant_age"]))
        birth_year = date.today().year - age
        pd_obj = _get_or_create(p45, "personalDetailsSummary")
        pd_obj["birthDate"] = f"01/01/{birth_year}"

    # 3. num_dependants
    if "num_dependants" in features and features["num_dependants"] is not None:
        pd_obj = _get_or_create(p45, "personalDetailsSummary")
        pd_obj["dependants"] = str(int(features["num_dependants"]))
        
    # 4. has_employer_detail
    if "has_employer_detail" in features:
        pd_obj = _get_or_create(p45, "personalDetailsSummary")
        if features["has_employer_detail"]:
            pd_obj["employerDetail"] = "OVERRIDDEN EMPLOYER"
        else:
            pd_obj["employerDetail"] = ""
            
    # 5. identity_verified
    if "identity_verified" in features:
        pd_obj = _get_or_create(p45, "personalDetailsSummary")
        if features["identity_verified"]:
            pd_obj["nationalIDNo"] = "OVERRIDDEN-ID-123"
        else:
            pd_obj["nationalIDNo"] = ""
            
    # 6. Credit Account Summary metrics
    ca_obj = _get_or_create(p45, "creditAccountSummary")
    
    if "total_outstanding_debt_ghs" in features and features["total_outstanding_debt_ghs"] is not None:
        ca_obj["totalOutstandingdebtGHS"] = str(features["total_outstanding_debt_ghs"])
        
    if "total_monthly_instalment_ghs" in features and features["total_monthly_instalment_ghs"] is not None:
        ca_obj["totalMonthlyInstalmentGHS"] = str(features["total_monthly_instalment_ghs"])
        
    if "num_active_accounts" in features and features["num_active_accounts"] is not None:
        ca_obj["totalActiveAccountsGHS"] = str(int(float(features["num_active_accounts"])))
        
    if "num_accounts_total" in features and features["num_accounts_total"] is not None:
        ca_obj["totalNumberofAccountsGHS"] = str(int(float(features["num_accounts_total"])))
        
    if "has_active_arrears" in features and features["has_active_arrears"] is not None:
        ca_obj["totalAccountInArrearGHS"] = "1" if features["has_active_arrears"] else "0"
        
    if "total_arrear_amount_ghs" in features and features["total_arrear_amount_ghs"] is not None:
        ca_obj["totalAmountInArrearGHS"] = str(features["total_arrear_amount_ghs"])

    # bounced cheques
    if "num_bounced_cheques" in features and features["num_bounced_cheques"] is not None:
        ca_obj["totalNumberofDishonouredGHS"] = str(int(float(features["num_bounced_cheques"])))

    # statuses -> creditAgreementSummary accountStatusCode
    if "has_written_off" in features or "has_charged_off" in features or "has_legal_handover" in features:
        agr = _first_or_empty("creditAgreementSummary")
        if features.get("has_written_off"):
            agr["accountStatusCode"] = "W"
        elif features.get("has_charged_off"):
            agr["accountStatusCode"] = "G"
        elif features.get("has_legal_handover"):
            agr["accountStatusCode"] = "L"

    # has_judgement
    if features.get("has_judgement"):
        if not p45.get("judgementSummary"):
            p45["judgementSummary"] = [{"judgementType": "OVERRIDDEN"}]
    else:
        p45["judgementSummary"] = []
        
    # has_adverse_default
    if features.get("has_adverse_default"):
        if not p45.get("defaults"):
            p45["defaults"] = [{"defaultType": "OVERRIDDEN"}]
    else:
        p45["defaults"] = []
        p45["adverseDetails"] = []

    # enquiries
    if "num_enquiries_12m" in features and features["num_enquiries_12m"] is not None:
        count = int(float(features["num_enquiries_12m"]))
        p45["enquiryHistory"] = [{"dateRequested": "01/01/2026 12:00:00", "enquiryReason": "OVERRIDDEN"} for _ in range(count)]

    # mobile product 49 modifications
    if p49:
        ca49_obj = _get_or_create(p49, "creditAccountSummary")
        if "mobile_max_loan_ghs" in features and features["mobile_max_loan_ghs"] is not None:
            ca49_obj["highestAmountTaken"] = str(features["mobile_max_loan_ghs"])
            
        if "mobile_loan_history_count" in features and features["mobile_loan_history_count"] is not None:
            count = int(float(features["mobile_loan_history_count"]))
            p49["detailedFacilityInfo"] = [{"accountStatusCode": "C"} for _ in range(count)]

    out["product_45"] = p45
    out["product_49"] = p49
    return out
