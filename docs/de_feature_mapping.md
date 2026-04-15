# XDS `xds_payload` fields → 30 ML feature columns

Paths are **nested JSON** under `xds_payload`. For **flat** silver rows, the column name equals the same dot-separated string (e.g. `consumer_full_report_45.personalDetailsSummary.birthDate`).

`[]` = each array element contributes.

Prefix **`45:`** = `consumer_full_report_45`. Prefix **`49:`** = `consumer_mobile_report_49`.

## Forward: XDS field → ML feature column(s)

| XDS payload field | ML feature column name(s) |
|-------------------|---------------------------|
| `45:personalDetailsSummary.birthDate` | `applicant_age` |
| `45:personalDetailsSummary.dependants` | `num_dependants` |
| `45:personalDetailsSummary.employerDetail` | `has_employer_detail` |
| `45:personalDetailsSummary.nationalIDNo` | `identity_verified` |
| `45:highestDelinquencyRating.highestDelinquencyRating` | `highest_delinquency_rating` |
| `45:creditAccountSummary.totalOutstandingdebtGHS` | `total_outstanding_debt_ghs` |
| `45:creditAccountSummary.totalMonthlyInstalmentGHS` | `total_monthly_instalment_ghs` *(summed with 49)* |
| `45:creditAccountSummary.totalActiveAccountsGHS` | `num_active_accounts` |
| `45:creditAccountSummary.totalNumberofAccountsGHS` | `num_accounts_total` |
| `45:creditAccountSummary.totalNumberofDishonouredGHS` | `num_bounced_cheques` *(with dudCheqEventSummary fallback)* |
| `45:creditAccountSummary.totalAccountInArrearGHS` | `has_active_arrears` |
| `45:creditAccountSummary.totalAmountInArrearGHS` | `total_arrear_amount_ghs` |
| `45:creditAgreementSummary[].accountStatusCode` | `num_closed_accounts_good`, `has_written_off`, `has_charged_off`, `has_legal_handover` |
| `45:creditAgreementSummary[].monthsInArrears` | `worst_arrears_24m` |
| `45:creditAgreementSummary[].openingBalanceAmt` + `currentBalanceAmt` | `utilisation_ratio` |
| `45:creditAgreementSummary[].dateAccountOpened` | `credit_age_months` |
| `45:creditAgreementSummary[].closedDate` | `num_closed_accounts_good` |
| `45:creditAgreementSummary[].accountType`/`loanType`/`subscriberName` | `product_diversity_score` |
| `45:accountMonthlyPaymentHistory[].m01…m24` | `months_on_time_24m`, `worst_arrears_24m`, `current_streak_on_time` |
| `45:enquiryHistory[].dateRequested` | `num_enquiries_3m`, `num_enquiries_12m` |
| `45:enquiryHistory` *(non-empty)* | `enquiry_reason_flags` |
| `45:judgementSummary` *(non-empty)* | `has_judgement` |
| `45:defaults` / `adverseDetails` *(non-empty)* | `has_adverse_default` |
| `45:dudCheqEventSummary` *(length, fallback)* | `num_bounced_cheques` |
| `45:addressHistory[].address`/`residentialAddress`/`postalAddress` | `address_stability` |
| `49:detailedFacilityInfo` *(length)* | `mobile_loan_history_count` |
| `49:detailedFacilityInfo[].accountStatusCode` | `has_written_off`, `has_charged_off`, `has_legal_handover` |
| `49:creditAccountSummary.highestAmountTaken` | `mobile_max_loan_ghs` |
| `49:creditAccountSummary.totalMonthlyInstalment` | `total_monthly_instalment_ghs` *(summed with 45)* |
