from __future__ import annotations

import io
import json
from datetime import date
from typing import Any, cast

import httpx
import pytest
from openpyxl import Workbook


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Name", "Age"])
    sheet.append(["Ada", 40])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


@pytest.mark.parametrize(
    ("name", "payload", "format_name"),
    [
        ("data.csv", b"Name,Age\nAda,40\n", "csv"),
        ("data.tsv", b"Name\tAge\nAda\t40\n", "tsv"),
        ("data.txt", b"Name|Age\nAda|40\n", "txt"),
        ("data.json", json.dumps([{"name": "Ada", "profile": {"age": 40}}]).encode(), "json"),
    ],
)
def test_parse_supported_text_formats(name, payload, format_name) -> None:
    from app.services.file_parser import parse_file

    result = parse_file(payload, name)
    assert result.format_detected == format_name
    assert result.row_count == 1
    assert result.to_dict()["columns"]


def test_parse_xlsx_and_error_cases() -> None:
    from app.services.file_parser import _clean_column_name, _flatten_dict, parse_file

    result = parse_file(_xlsx_bytes(), "data.xlsx")
    assert result.columns == ["name", "age"]
    assert result.sample_rows == [["Ada", "40"]]
    assert _clean_column_name(" Full Name ") == "full_name"
    assert _flatten_dict({"a": {"b": 1}}) == {"a_b": 1}
    with pytest.raises(ValueError, match="Unsupported"):
        parse_file(b"x", "data.pdf")
    with pytest.raises(ValueError, match="header row"):
        parse_file(b"name\n", "data.csv")
    with pytest.raises(ValueError, match="top-level"):
        parse_file(b'{"name":"Ada"}', "data.json")
    with pytest.raises(ValueError, match="non-empty"):
        parse_file(b"[]", "data.json")


def test_transform_csv_quality_casting_and_derived_feature() -> None:
    from app.services.transform_service import transform_data

    data = (
        b"Income,Repayment,Defaulted,Age\n"
        b"5000,1000,true,40\n"
        b"5000,1000,true,bad\n"
        b"5000,1000,true,40\n"
    )
    mappings = [
        {"source_field": "income", "target_feature": "monthly_income"},
        {"source_field": "repayment", "target_feature": "monthly_loan_repayment"},
        {"source_field": "defaulted", "target_feature": "default_flag"},
        {"source_field": "age", "target_feature": "applicant_age"},
    ]
    result = transform_data(data, "data.csv", mappings)
    assert result.record_count == 3
    assert result.valid_record_count == 3
    assert b"debt_to_income_ratio" not in result.csv_bytes  # derived but only mapped columns export
    assert result.quality_report["type_error_count"] >= 1
    assert result.quality_report["duplicate_rows"] >= 1
    assert result.warnings
    assert 0 <= result.quality_score <= 1


class StubResponse:
    def __init__(self, status_code: int, payload, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class StubClient:
    def __init__(self, responses: list[StubResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Any]] = []

    async def get(self, path: str, params=None):
        self.calls.append((path, params))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_xds_login_match_reports_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import xds_client

    monkeypatch.setattr(xds_client.settings, "xds_username", "user")
    monkeypatch.setattr(xds_client.settings, "xds_password", "pass")
    client = xds_client.XDSClient()
    stub = StubClient(
        [
            StubResponse(200, {"dataTicket": "ticket"}),
            StubResponse(200, [{"consumerID": "c1"}]),
            StubResponse(200, [{"consumerID": "m1"}]),
            StubResponse(200, {"report": 45}),
            StubResponse(200, {"report": 49}),
        ]
    )
    http_client = cast(httpx.AsyncClient, stub)
    assert await client.login(http_client) == "ticket"
    assert await client.consumer_match(http_client, date(1990, 1, 1), identification="GHA")
    assert await client.mobile_match(http_client, date(1990, 1, 1), telephone_number="233")
    full_report = await client.get_consumer_full_credit_report(http_client, "e", "c", "m")
    assert full_report["report"] == 45
    assert (await client.get_consumer_mobile_credit(http_client, "e", "c", "m"))["report"] == 49

    with pytest.raises(xds_client.XDSError, match="requires"):
        await client.consumer_match(http_client, date(1990, 1, 1))
    with pytest.raises(xds_client.XDSError, match="requires"):
        await client.mobile_match(http_client, date(1990, 1, 1))
    with pytest.raises(xds_client.XDSError, match="HTTP 500"):
        await client.login(cast(httpx.AsyncClient, StubClient([StubResponse(500, {})])))


@pytest.mark.asyncio
async def test_xds_product_flows_and_match_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.xds_client import DEFAULT_ENQUIRY_REASON, XDSClient, XDSError

    client = XDSClient()
    no_record = [{"response": {"message": "No records found"}}]
    assert client._parse_match_response(no_record) == []
    assert client._parse_match_response({"consumerID": "1"}) == [{"consumerID": "1"}]
    assert client._parse_match_response("bad") == []

    one = [{"enquiryID": "e", "consumerID": "c", "matchingEngineID": "m"}]

    async def consumer_match(**kwargs):
        return one

    async def mobile_match(**kwargs):
        return one + one

    async def report(**kwargs):
        return {"ok": True}

    monkeypatch.setattr(client, "consumer_match", consumer_match)
    monkeypatch.setattr(client, "mobile_match", mobile_match)
    monkeypatch.setattr(client, "get_consumer_full_credit_report", report)
    p45 = await client._fetch_product_45(
        cast(httpx.AsyncClient, object()), date.today(), "Ada", "ID", None, DEFAULT_ENQUIRY_REASON
    )
    p49 = await client._fetch_product_49(
        cast(httpx.AsyncClient, object()), date.today(), "Ada", "ID", None, DEFAULT_ENQUIRY_REASON
    )
    assert p45["status"] == "HIT" and p45["report"] == {"ok": True}
    assert p49["status"] == "MULTIPLE_MATCH"

    async def failed(**kwargs):
        raise XDSError("down")

    monkeypatch.setattr(client, "consumer_match", failed)
    failed_result = await client._fetch_product_45(
        cast(httpx.AsyncClient, object()), date.today(), "Ada", "ID", None, DEFAULT_ENQUIRY_REASON
    )
    assert failed_result["status"] == "NO_RECORD"


@pytest.mark.asyncio
async def test_fire_de_inference_success_failure_and_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    from app.services import scoring_pipeline

    class FakeAsyncClient:
        response = httpx.Response(202)
        exception: Exception | None = None

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            if self.exception:
                raise self.exception
            return self.response

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    await scoring_pipeline.fire_de_inference("SCR-1", {"x": 1})
    FakeAsyncClient.response = httpx.Response(500, text="bad")
    with pytest.raises(RuntimeError, match="HTTP 500: bad"):
        await scoring_pipeline.fire_de_inference("SCR-2", {"x": 2})
    FakeAsyncClient.exception = RuntimeError("network")
    with pytest.raises(RuntimeError, match="network"):
        await scoring_pipeline.fire_de_inference("SCR-3", {"x": 3})
