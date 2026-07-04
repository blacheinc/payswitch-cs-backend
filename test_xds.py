"""Comprehensive XDS API test — validates against official docs (v2).

Tests:
  1. Login
  2. ConsumerMatch with text enquiryReason (docs-compliant)
  3. GetConsumerFullCreditReport (Product 45)
  4. MobileMatch (Product 49)
  5. GetConsumerMobileCredit (Product 49 report)
  6. Parse all data segments from docs
  7. Edge case handling
"""

import asyncio
import json
import os
import httpx

XDS_BASE_URL = os.environ.get(
    "XDS_BASE_URL", "https://www.online.xdsdata.com/XdsDataRestApi/Api"
)
USERNAME = os.environ.get("XDS_USERNAME", "")
PASSWORD = os.environ.get("XDS_PASSWORD", "")

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
results = []


def log(status, test, detail=""):
    results.append((status, test))
    print(f"  {status} {test}" + (f" — {detail}" if detail else ""))


async def test_all():
    async with httpx.AsyncClient(base_url=XDS_BASE_URL, timeout=30.0) as client:

        # ═══════════════════════════════════════════════════════════
        print("\n1. LOGIN")
        print("=" * 60)
        resp = await client.get("/login", params={"username": USERNAME, "password": PASSWORD})
        data = resp.json()
        ticket = data.get("dataTicket")
        log(PASS if ticket else FAIL, "Login returns DataTicket", f"status={resp.status_code}")
        log(PASS if data.get("statusCode") == 200 else FAIL, "Login statusCode is 200")
        if not ticket:
            print("Cannot continue without ticket")
            return

        # ═══════════════════════════════════════════════════════════
        print("\n2. CONSUMER MATCH (Product 45) — text enquiryReason")
        print("=" * 60)

        # Test with official text enquiryReason (docs source of truth)
        r_text = await client.get("/getconsumermatch", params={
            "dataticket": ticket,
            "enquiryReason": "Application for credit by a borrower",
            "ConsumerName": "John",
            "DateOfBirth": "1990/05/15",
        })
        d_text = r_text.json()
        is_list = isinstance(d_text, list) and len(d_text) > 0
        log(PASS if is_list else FAIL, "Text enquiryReason returns matches", f"count={len(d_text) if is_list else 0}")

        # Check valid match fields from docs
        if is_list:
            m = d_text[0]
            resp_msg = m.get("response", {}).get("message", "")
            has_match = "no records" not in resp_msg.lower()
            log(PASS if has_match else WARN, "Match response has records")

            doc_fields = ["matchingEngineID", "enquiryID", "consumerID", "reference",
                          "idNo", "passportNo", "socialSecurityNo", "voterIDNo",
                          "driversLicenseNo", "firstName", "surname", "otherNames",
                          "address", "birthDate", "genderInd", "accountNo"]
            missing = [f for f in doc_fields if f not in m]
            log(PASS if not missing else FAIL, "Match has all doc fields", f"missing={missing}" if missing else "all 16 present")

        # Test identification + DOB combo (recommended by docs)
        r_id = await client.get("/getconsumermatch", params={
            "dataticket": ticket,
            "enquiryReason": "Application for credit by a borrower",
            "identification": "3611003033",
            "DateOfBirth": "1990/05/15",
        })
        d_id = r_id.json()
        log(PASS if isinstance(d_id, list) else FAIL, "ID+DOB combo works (docs recommended)")

        # Pick a match for report testing
        match = None
        if is_list:
            for m in d_text:
                if m.get("consumerID") == 2580957:
                    match = m
                    break
            if not match:
                match = d_text[0]

        # ═══════════════════════════════════════════════════════════
        print("\n3. FULL CREDIT REPORT (Product 45)")
        print("=" * 60)

        if match:
            r_report = await client.get("/GetConsumerFullCreditReport", params={
                "EnquiryID": match["enquiryID"],
                "ConsumerID": match["consumerID"],
                "DataTicket": ticket,
                "MatchingEngineID": match["matchingEngineID"],
            })
            report = r_report.json()
            log(PASS if r_report.status_code == 200 else FAIL, "Report HTTP 200")
            log(PASS if report.get("response", {}).get("message") == "Success" else FAIL, "Report message='Success'")

            # Check all data segments from docs
            doc_segments = {
                "personalDetailsSummary": "Personal Details",
                "highestDelinquencyRating": "XDS Delinquency Rating",
                "creditAccountSummary": "Credit Account Summary",
                "subjectList": "Subjects List",
                "accountRating": "Credit Account Rating",
                "creditAgreementSummary": "Credit Agreement Summary",
                "accountMonthlyPaymentHistory": "Monthly Payment History",
                "jointLoanAccountDetails": "Joint Loan Details",
                "guarantorCount": "Guarantor Count",
                "guarantorDetails": "Guarantor Details",
                "defaults": "Public Info Defaults",
                "adverseDetails": "Adverse Details",
                "dudCheqEventSummary": "Dishonoured Cheques",
                "nameHistory": "Name History",
                "identificationHistory": "ID History",
                "addressHistory": "Address History",
                "employmentHistory": "Employment History",
                "telephoneHistory": "Telephone History",
                "enquiryHistory": "Enquiry History",
            }
            for key, label in doc_segments.items():
                present = key in report
                val = report.get(key)
                detail = ""
                if isinstance(val, list):
                    detail = f"count={len(val)}"
                elif isinstance(val, dict):
                    detail = f"keys={len(val)}"
                log(PASS if present else WARN, f"Segment: {label} ({key})", detail if present else "MISSING")

            # Validate personalDetailsSummary fields against docs
            personal = report.get("personalDetailsSummary", {})
            if isinstance(personal, dict):
                doc_personal_fields = [
                    "consumerID", "surname", "firstName", "otherNames", "birthDate",
                    "gender", "nationalIDNo", "passportNo", "driversLicenseNo",
                    "residentialAddress1", "cellularNo", "employerDetail",
                    "emailAddress", "maritalStatus", "dependants", "nationality",
                ]
                present_fields = [f for f in doc_personal_fields if f in personal]
                log(PASS, f"Personal fields present: {len(present_fields)}/{len(doc_personal_fields)}")

            # Validate creditAccountSummary fields
            summary = report.get("creditAccountSummary", {})
            if isinstance(summary, dict):
                doc_summary_fields = [
                    "totalNumberofAccountsGHS", "totalMonthlyInstalmentGHS",
                    "totalOutstandingdebtGHS", "totalAccountInArrearGHS",
                    "totalAmountInArrearGHS", "totalaccountinGoodconditionGHS",
                    "totalNumberofJudgementGHS", "totalJudgementAmountGHS",
                    "totalNumberofDishonouredGHS",
                ]
                present = [f for f in doc_summary_fields if f in summary]
                log(PASS, f"Credit summary fields: {len(present)}/{len(doc_summary_fields)}")

                # Test comma-formatted number parsing
                debt = summary.get("totalOutstandingdebtGHS", "0")
                try:
                    parsed = float(str(debt).replace(",", ""))
                    log(PASS, f"Comma-formatted parsing", f"'{debt}' → {parsed}")
                except ValueError:
                    log(FAIL, f"Comma-formatted parsing failed", f"'{debt}'")

            # Delinquency rating format
            del_obj = report.get("highestDelinquencyRating", {})
            if isinstance(del_obj, dict):
                rating = del_obj.get("highestDelinquencyRating", "")
                is_text = rating.lower() in ("good", "fairly good", "poor", "very poor")
                is_numeric = rating in ("0", "1", "2", "3", "4")
                log(PASS if is_text or is_numeric else WARN,
                    f"Delinquency format", f"value='{rating}' ({'text' if is_text else 'numeric' if is_numeric else 'unknown'})")

            # Payment history m01-m24 format
            payment = report.get("accountMonthlyPaymentHistory", [])
            if isinstance(payment, list) and payment:
                ph = payment[0]
                has_m_keys = all(f"m{str(i).zfill(2)}" in ph for i in range(1, 25))
                has_header_keys = all(f"mH{str(i).zfill(2)}" in ph for i in range(1, 25))
                log(PASS if has_m_keys else FAIL, "Payment history m01-m24 keys present")
                log(PASS if has_header_keys else FAIL, "Payment history mH01-mH24 header keys present")

            # Facility status codes from docs
            accounts = report.get("creditAgreementSummary", [])
            if isinstance(accounts, list) and accounts:
                valid_codes = {"C", "D", "E", "G", "L", "N", "P", "RV", "T", "V", "W", "X", "Z", "A"}
                for acct in accounts:
                    code = acct.get("accountStatusCode", "")
                    log(PASS if code in valid_codes else WARN,
                        f"Account status code", f"'{code}' for {acct.get('subscriberName', '?')}")

        # ═══════════════════════════════════════════════════════════
        print("\n4. MOBILE MATCH (Product 49)")
        print("=" * 60)

        r_mob = await client.get("/GetMobileMatch", params={
            "dataticket": ticket,
            "enquiryReason": "Application for credit by a borrower",
            "ConsumerName": "John",
            "DateOfBirth": "1990/05/15",
            "TelephoneNumber": "0244123456",
        })
        d_mob = r_mob.json()
        log(PASS if r_mob.status_code == 200 else FAIL, "MobileMatch HTTP 200")
        if isinstance(d_mob, list) and d_mob:
            msg = d_mob[0].get("response", {}).get("message", "")
            is_no_record = "no records" in msg.lower()
            log(PASS, "MobileMatch response", f"{'NO_RECORD' if is_no_record else f'HIT ({len(d_mob)} matches)'}")

        # Test identification + phone combo (docs recommended)
        r_mob2 = await client.get("/GetMobileMatch", params={
            "dataticket": ticket,
            "enquiryReason": "Application for credit by a borrower",
            "identification": "3611003033",
            "TelephoneNumber": "0244123456",
        })
        log(PASS if r_mob2.status_code == 200 else FAIL, "ID+Phone combo works (docs recommended)")

        # ═══════════════════════════════════════════════════════════
        print("\n5. MOBILE CREDIT REPORT (Product 49)")
        print("=" * 60)

        # Use P45 match to test GetConsumerMobileCredit endpoint
        if match:
            r_mob_report = await client.get("/GetConsumerMobileCredit", params={
                "EnquiryID": match["enquiryID"],
                "ConsumerID": match["consumerID"],
                "DataTicket": ticket,
                "MatchingEngineID": match["matchingEngineID"],
            })
            log(PASS if r_mob_report.status_code == 200 else FAIL,
                "GetConsumerMobileCredit HTTP 200")
            mob_report = r_mob_report.json()
            mob_msg = mob_report.get("response", {}).get("message", "")
            log(PASS if mob_msg == "Success" else WARN,
                "Mobile report response", f"message='{mob_msg}'")

            # Check it has the same segments as P45 (docs say same report type)
            mob_has_personal = "personalDetailsSummary" in mob_report
            mob_has_credit = "creditAccountSummary" in mob_report or "creditAgreementSummary" in mob_report
            log(PASS if mob_has_personal else WARN, "Mobile report has personalDetailsSummary")
            log(PASS if mob_has_credit else WARN, "Mobile report has credit data")

        # ═══════════════════════════════════════════════════════════
        print("\n6. EDGE CASES")
        print("=" * 60)

        # No-record test
        r_none = await client.get("/getconsumermatch", params={
            "dataticket": ticket,
            "enquiryReason": "Application for credit by a borrower",
            "ConsumerName": "ZzzNonexistentPerson",
            "DateOfBirth": "2005/01/01",
        })
        d_none = r_none.json()
        if isinstance(d_none, list) and d_none:
            msg = d_none[0].get("response", {}).get("message", "")
            log(PASS if "no records" in msg.lower() else FAIL, "NO_RECORD returns correct message", f"'{msg}'")

        # Expired/bad ticket
        r_bad = await client.get("/getconsumermatch", params={
            "dataticket": "invalid_ticket_12345",
            "enquiryReason": "Application for credit by a borrower",
            "ConsumerName": "John",
            "DateOfBirth": "1990/05/15",
        })
        log(PASS if r_bad.status_code != 200 or "error" in str(r_bad.json()).lower() or "unauthorized" in str(r_bad.json()).lower() else WARN,
            "Bad ticket handled", f"status={r_bad.status_code}")

        # ═══════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        passed = sum(1 for s, _ in results if s == PASS)
        warned = sum(1 for s, _ in results if s == WARN)
        failed = sum(1 for s, _ in results if s == FAIL)
        print(f"  {PASS} Passed: {passed}")
        print(f"  {WARN} Warnings: {warned}")
        print(f"  {FAIL} Failed: {failed}")
        print(f"  Total: {len(results)}")


if __name__ == "__main__":
    asyncio.run(test_all())
