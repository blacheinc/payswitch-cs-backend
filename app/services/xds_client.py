"""XDS Credit Bureau REST API client.

Implements the 3-step flow: Login → Match → Result
for Products 45 (Consumer Full Credit) and 49 (Consumer Mobile Credit).

Each product is an independent flow with its own match/report lifecycle.

Base URL: https://www.online.xdsdata.com/XdsDataRestApi/Api
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

XDS_BASE_URL = "https://www.online.xdsdata.com/XdsDataRestApi/Api"
XDS_TIMEOUT = 30.0  # seconds

# Official enquiry reason values (from XDS API docs v2)
ENQUIRY_REASONS = [
    "Application for credit by a borrower",
    "Reviewing of existing credit facilities",
    "Opening of new accounts (as part of KYC principle)",
    "Funds transfer of GHS 1,000,000 and above",
    "Prospective/current employee checks",
    "Tenancy contracts (for identification purposes)",
    "Grant/review of insurance policies",
    "Acceptance of guarantee(s)",
    "Application for contracts/pre-paid services (telephone etc.)",
    "Court judgement",
    "Credit scoring of the client by credit bureau",
    "A written consent from the client",
    "Legislation",
    "Recoveries",
    "Test (for testing purposes)",
]
DEFAULT_ENQUIRY_REASON = "Application for credit by a borrower"


class XDSError(Exception):
    """Raised when an XDS API call fails."""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class XDSClient:
    """Client for the XDS Data Ghana RESTful API."""

    def __init__(self) -> None:
        self._data_ticket: str | None = None
        self._ticket_obtained_at: datetime | None = None

    def _make_client(self) -> httpx.AsyncClient:
        """Create a shared httpx client (caller must use as context manager)."""
        return httpx.AsyncClient(
            base_url=XDS_BASE_URL,
            timeout=XDS_TIMEOUT,
            verify=True,
        )

    # ── Step 1: Login ────────────────────────────────────────────────────

    async def login(self, client: httpx.AsyncClient) -> str:
        """Authenticate and obtain a DataTicket."""
        username = settings.xds_username
        password = settings.xds_password

        if not username or not password:
            raise XDSError("XDS credentials not configured")

        response = await client.get(
            "/login",
            params={"username": username, "password": password},
        )

        if response.status_code != 200:
            raise XDSError(
                f"Login failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        data = response.json()
        ticket = data.get("dataTicket")
        if not ticket:
            error_msg = data.get("message", "No DataTicket returned")
            raise XDSError(f"Login failed: {error_msg}")

        self._data_ticket = ticket
        self._ticket_obtained_at = datetime.now(timezone.utc)
        logger.info("[XDS] Login successful, DataTicket obtained")
        return ticket

    # ── Step 2: Consumer Match (Product 45) ──────────────────────────────

    async def consumer_match(
        self,
        client: httpx.AsyncClient,
        date_of_birth: date,
        consumer_name: str | None = None,
        identification: str | None = None,
        account_number: str | None = None,
        enquiry_reason: str = DEFAULT_ENQUIRY_REASON,
    ) -> list[dict[str, Any]]:
        """Perform a consumer match (Product 45).

        At least one of consumer_name or identification must be provided
        along with date_of_birth. The API supports:
          - Identification + DateOfBirth (recommended)
          - ConsumerName + DateOfBirth
          - All three combined
        """
        if not consumer_name and not identification:
            raise XDSError("Consumer match requires at least a name or identification")

        params: dict[str, str] = {
            "dataticket": self._data_ticket or "",
            "enquiryReason": enquiry_reason,
            "DateOfBirth": date_of_birth.strftime("%Y/%m/%d"),
        }
        if consumer_name:
            params["ConsumerName"] = consumer_name
        if identification:
            params["identification"] = identification
        if account_number:
            params["AccountNumber"] = account_number

        response = await client.get("/getconsumermatch", params=params)

        if response.status_code != 200:
            raise XDSError(
                f"ConsumerMatch failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        return self._parse_match_response(response.json())

    # ── Step 2b: Mobile Match (Product 49) ───────────────────────────────

    async def mobile_match(
        self,
        client: httpx.AsyncClient,
        date_of_birth: date,
        consumer_name: str | None = None,
        identification: str | None = None,
        telephone_number: str | None = None,
        enquiry_reason: str = DEFAULT_ENQUIRY_REASON,
    ) -> list[dict[str, Any]]:
        """Perform a mobile match (Product 49).

        Supports ConsumerName + DateOfBirth, Identification + DateOfBirth,
        or TelephoneNumber-based lookup.
        """
        if not consumer_name and not identification and not telephone_number:
            raise XDSError("Mobile match requires a name, identification, or phone")

        params: dict[str, str] = {
            "dataticket": self._data_ticket or "",
            "enquiryReason": enquiry_reason,
            "DateOfBirth": date_of_birth.strftime("%Y/%m/%d"),
        }
        if consumer_name:
            params["ConsumerName"] = consumer_name
        if identification:
            params["identification"] = identification
        if telephone_number:
            params["TelephoneNumber"] = telephone_number

        response = await client.get("/GetMobileMatch", params=params)

        if response.status_code != 200:
            raise XDSError(
                f"MobileMatch failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        return self._parse_match_response(response.json())

    # ── Step 3: Get Reports ──────────────────────────────────────────────

    async def get_consumer_full_credit_report(
        self,
        client: httpx.AsyncClient,
        enquiry_id: str,
        consumer_id: str,
        matching_engine_id: str,
    ) -> dict[str, Any]:
        """Get the full consumer credit report (Product 45)."""
        response = await client.get(
            "/GetConsumerFullCreditReport",
            params={
                "EnquiryID": enquiry_id,
                "ConsumerID": consumer_id,
                "DataTicket": self._data_ticket or "",
                "MatchingEngineID": matching_engine_id,
            },
        )

        if response.status_code != 200:
            raise XDSError(
                f"ConsumerFullCreditReport failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        return response.json()

    async def get_consumer_mobile_credit(
        self,
        client: httpx.AsyncClient,
        enquiry_id: str,
        consumer_id: str,
        matching_engine_id: str,
    ) -> dict[str, Any]:
        """Get the mobile credit report (Product 49)."""
        response = await client.get(
            "/GetConsumerMobileCredit",
            params={
                "EnquiryID": enquiry_id,
                "ConsumerID": consumer_id,
                "DataTicket": self._data_ticket or "",
                "MatchingEngineID": matching_engine_id,
            },
        )

        if response.status_code != 200:
            raise XDSError(
                f"ConsumerMobileCredit failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        return response.json()

    # ── Combined Flow ────────────────────────────────────────────────────

    async def full_bureau_lookup(
        self,
        date_of_birth: date,
        consumer_name: str | None = None,
        identification: str | None = None,
        phone_number: str | None = None,
        account_number: str | None = None,
        enquiry_reason: str = DEFAULT_ENQUIRY_REASON,
    ) -> dict[str, Any]:
        """Complete bureau lookup: login, match, and fetch Product 45 + 49.

        Product 45 and 49 are treated as **independent** flows. Each has
        its own match status, matches list, and report. The caller decides
        how to combine the results.

        Returns:
            {
                "product_45": {
                    "status": "HIT" | "NO_RECORD" | "MULTIPLE_MATCH",
                    "matches": [...],
                    "report": {...} or None,
                },
                "product_49": {
                    "status": "HIT" | "NO_RECORD" | "MULTIPLE_MATCH",
                    "matches": [...],
                    "report": {...} or None,
                },
            }
        """
        async with self._make_client() as client:
            # Login (one ticket for both products)
            await self.login(client)

            # ── Product 45: Independent flow ─────────────────────────────
            p45_result = await self._fetch_product_45(
                client=client,
                date_of_birth=date_of_birth,
                consumer_name=consumer_name,
                identification=identification,
                account_number=account_number,
                enquiry_reason=enquiry_reason,
            )

            # ── Product 49: Independent flow ─────────────────────────────
            p49_result = await self._fetch_product_49(
                client=client,
                date_of_birth=date_of_birth,
                consumer_name=consumer_name,
                identification=identification,
                phone_number=phone_number,
                enquiry_reason=enquiry_reason,
            )

        return {
            "product_45": p45_result,
            "product_49": p49_result,
        }

    async def _fetch_product_45(
        self,
        client: httpx.AsyncClient,
        date_of_birth: date,
        consumer_name: str | None,
        identification: str | None,
        account_number: str | None,
        enquiry_reason: str,
    ) -> dict[str, Any]:
        """Run the full Product 45 flow: match → report."""
        result: dict[str, Any] = {
            "status": "NO_RECORD",
            "matches": [],
            "report": None,
        }

        try:
            matches = await self.consumer_match(
                client=client,
                date_of_birth=date_of_birth,
                consumer_name=consumer_name,
                identification=identification,
                account_number=account_number,
                enquiry_reason=enquiry_reason,
            )
        except XDSError as e:
            logger.warning(f"[XDS] Product 45 match failed: {e.message}")
            return result

        if not matches:
            return result

        result["matches"] = matches

        if len(matches) > 1:
            result["status"] = "MULTIPLE_MATCH"
            return result

        # Single match — fetch report
        match = matches[0]
        enquiry_id = match.get("enquiryID", "")
        consumer_id = match.get("consumerID", "")
        matching_engine_id = match.get("matchingEngineID", "")

        if enquiry_id and consumer_id and matching_engine_id:
            try:
                report = await self.get_consumer_full_credit_report(
                    client=client,
                    enquiry_id=enquiry_id,
                    consumer_id=consumer_id,
                    matching_engine_id=matching_engine_id,
                )
                result["status"] = "HIT"
                result["report"] = report
            except XDSError as e:
                logger.warning(f"[XDS] Product 45 report failed: {e.message}")

        return result

    async def _fetch_product_49(
        self,
        client: httpx.AsyncClient,
        date_of_birth: date,
        consumer_name: str | None,
        identification: str | None,
        phone_number: str | None,
        enquiry_reason: str,
    ) -> dict[str, Any]:
        """Run the full Product 49 flow: match → report."""
        result: dict[str, Any] = {
            "status": "NO_RECORD",
            "matches": [],
            "report": None,
        }

        # Product 49 needs at least one identifier
        if not consumer_name and not identification and not phone_number:
            return result

        try:
            matches = await self.mobile_match(
                client=client,
                date_of_birth=date_of_birth,
                consumer_name=consumer_name,
                identification=identification,
                telephone_number=phone_number,
                enquiry_reason=enquiry_reason,
            )
        except XDSError as e:
            logger.warning(f"[XDS] Product 49 match failed: {e.message}")
            return result

        if not matches:
            return result

        result["matches"] = matches

        if len(matches) > 1:
            result["status"] = "MULTIPLE_MATCH"
            return result

        # Single match — fetch report
        match = matches[0]
        enquiry_id = match.get("enquiryID", "")
        consumer_id = match.get("consumerID", "")
        matching_engine_id = match.get("matchingEngineID", "")

        if enquiry_id and consumer_id and matching_engine_id:
            try:
                report = await self.get_consumer_mobile_credit(
                    client=client,
                    enquiry_id=enquiry_id,
                    consumer_id=consumer_id,
                    matching_engine_id=matching_engine_id,
                )
                result["status"] = "HIT"
                result["report"] = report
            except XDSError as e:
                logger.warning(f"[XDS] Product 49 report failed: {e.message}")

        return result

    # ── Helpers ──────────────────────────────────────────────────────────

    def _parse_match_response(self, data: Any) -> list[dict[str, Any]]:
        """Parse match response, handling both single and multiple matches."""
        if isinstance(data, list):
            # Check for "No records found" message
            if len(data) == 1:
                resp = data[0].get("response", {})
                msg = resp.get("message", "")
                if "no records found" in msg.lower():
                    return []
            return data
        elif isinstance(data, dict):
            resp = data.get("response", {})
            msg = resp.get("message", "")
            if "no records found" in msg.lower():
                return []
            return [data]
        return []


# ── Singleton ───────────────────────────────────────────────────────────────

_xds_client: XDSClient | None = None


def get_xds_client() -> XDSClient:
    global _xds_client
    if _xds_client is None:
        _xds_client = XDSClient()
    return _xds_client
