"""Multi-format file parser for training data uploads.

Handles CSV, TSV, TXT (delimited), JSON, and XLSX.
Extracts column headers, sample rows, and row count.
Supports nested JSON (flattens keys).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Max rows to sample for mapping preview
SAMPLE_ROW_COUNT = 10
# Max rows to count (avoid OOM on giant files)
MAX_ROW_SCAN = 500_000


class ParseResult:
    """Result of parsing an uploaded file."""

    def __init__(
        self,
        columns: list[str],
        sample_rows: list[list[Any]],
        row_count: int,
        format_detected: str,
    ):
        self.columns = columns
        self.sample_rows = sample_rows
        self.row_count = row_count
        self.format_detected = format_detected

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "sample_rows": self.sample_rows,
            "row_count": self.row_count,
            "format_detected": self.format_detected,
        }


def parse_file(file_bytes: bytes, file_name: str) -> ParseResult:
    """Auto-detect format and parse the uploaded file.

    Args:
        file_bytes: Raw file content.
        file_name: Original filename (used for format detection).

    Returns:
        ParseResult with columns, sample rows, and row count.

    Raises:
        ValueError: If the file format is unsupported or unparseable.
    """
    ext = Path(file_name).suffix.lower().lstrip(".")

    parser_map = {
        "csv": _parse_csv,
        "tsv": _parse_tsv,
        "txt": _parse_delimited_auto,
        "json": _parse_json,
        "xlsx": _parse_xlsx,
    }

    parser = parser_map.get(ext)
    if not parser:
        raise ValueError(
            f"Unsupported file format '.{ext}'. "
            f"Accepted formats: {', '.join(parser_map.keys())}"
        )

    try:
        return parser(file_bytes)
    except Exception as e:
        logger.exception(f"Failed to parse {file_name}")
        raise ValueError(f"Failed to parse '{file_name}': {e}") from e


def _parse_csv(data: bytes) -> ParseResult:
    """Parse CSV with dialect sniffing."""
    text = data.decode("utf-8-sig")  # Handle BOM
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(text[:8192])
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]

    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)

    if len(rows) < 2:
        raise ValueError("CSV file must have a header row and at least one data row")

    columns = [_clean_column_name(c) for c in rows[0]]
    data_rows = rows[1:]
    sample = data_rows[:SAMPLE_ROW_COUNT]

    return ParseResult(
        columns=columns,
        sample_rows=sample,
        row_count=len(data_rows),
        format_detected="csv",
    )


def _parse_tsv(data: bytes) -> ParseResult:
    """Parse TSV (tab-separated)."""
    text = data.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    rows = list(reader)

    if len(rows) < 2:
        raise ValueError("TSV file must have a header row and at least one data row")

    columns = [_clean_column_name(c) for c in rows[0]]
    data_rows = rows[1:]
    sample = data_rows[:SAMPLE_ROW_COUNT]

    return ParseResult(
        columns=columns,
        sample_rows=sample,
        row_count=len(data_rows),
        format_detected="tsv",
    )


def _parse_delimited_auto(data: bytes) -> ParseResult:
    """Auto-detect delimiter for .txt files."""
    text = data.decode("utf-8-sig")
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(text[:8192])
    except csv.Error:
        # Fall back to comma
        dialect = csv.excel  # type: ignore[assignment]

    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)

    if len(rows) < 2:
        raise ValueError("TXT file must have a header row and at least one data row")

    columns = [_clean_column_name(c) for c in rows[0]]
    data_rows = rows[1:]
    sample = data_rows[:SAMPLE_ROW_COUNT]

    return ParseResult(
        columns=columns,
        sample_rows=sample,
        row_count=len(data_rows),
        format_detected="txt",
    )


def _parse_json(data: bytes) -> ParseResult:
    """Parse JSON — supports array-of-objects and nested structures."""
    text = data.decode("utf-8-sig")
    parsed = json.loads(text)

    # Handle both array and single-object-with-array
    if isinstance(parsed, dict):
        # Look for the first array key (e.g., {"records": [...]})
        for key, val in parsed.items():
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                parsed = val
                break
        else:
            raise ValueError(
                "JSON file must be an array of objects or contain a top-level key with an array"
            )

    if not isinstance(parsed, list) or len(parsed) == 0:
        raise ValueError("JSON file must contain a non-empty array of records")

    # Flatten nested keys: {"customer": {"age": 35}} → {"customer_age": 35}
    flat_records = [_flatten_dict(record) for record in parsed]

    columns = list(flat_records[0].keys())
    # Merge all keys (in case records have different keys)
    all_keys: dict[str, None] = {}
    for record in flat_records[:100]:
        for key in record:
            all_keys[key] = None
    columns = list(all_keys.keys())

    sample_rows = [
        [str(record.get(col, "")) for col in columns]
        for record in flat_records[:SAMPLE_ROW_COUNT]
    ]

    return ParseResult(
        columns=columns,
        sample_rows=sample_rows,
        row_count=len(flat_records),
        format_detected="json",
    )


def _parse_xlsx(data: bytes) -> ParseResult:
    """Parse XLSX (first sheet)."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ValueError(
            "openpyxl is required for XLSX parsing. Install with: pip install openpyxl"
        )

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        raise ValueError("XLSX file has no active worksheet")

    rows: list[list[Any]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= MAX_ROW_SCAN:
            break
        rows.append([str(cell) if cell is not None else "" for cell in row])

    wb.close()

    if len(rows) < 2:
        raise ValueError("XLSX file must have a header row and at least one data row")

    columns = [_clean_column_name(c) for c in rows[0]]
    data_rows = rows[1:]
    sample = data_rows[:SAMPLE_ROW_COUNT]

    return ParseResult(
        columns=columns,
        sample_rows=sample,
        row_count=len(data_rows),
        format_detected="xlsx",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _clean_column_name(name: str) -> str:
    """Normalize column name: strip whitespace, lowercase."""
    return str(name).strip().lower().replace(" ", "_")


def _flatten_dict(
    d: dict[str, Any], parent_key: str = "", sep: str = "_"
) -> dict[str, Any]:
    """Flatten nested dict: {"customer": {"age": 35}} → {"customer_age": 35}."""
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    return dict(items)
