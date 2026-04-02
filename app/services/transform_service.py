"""Transform service — applies approved field mappings to raw data.

After admin approves the mapping, this service:
1. Loads raw file from Blob Storage
2. Applies column renames (bank field → our feature)
3. Casts types
4. Calculates derived features (e.g. debt_to_income_ratio)
5. Runs quality checks
6. Writes clean CSV to Blob: transformed/{source}/{upload_id}.csv
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from app.services.file_parser import parse_file
from app.services.training_data_features import (
    MIN_FEATURES_MAPPED,
    MIN_RECORD_COUNT,
    RECOMMENDED_RECORD_COUNT,
    TARGET_FEATURES,
    TARGET_VARIABLE,
    TYPE_CAST_MAP,
)

logger = logging.getLogger(__name__)


class TransformResult:
    """Result of transforming raw data to target schema."""

    def __init__(
        self,
        csv_bytes: bytes,
        record_count: int,
        valid_record_count: int,
        quality_report: dict[str, Any],
        quality_score: float,
        warnings: list[str],
    ):
        self.csv_bytes = csv_bytes
        self.record_count = record_count
        self.valid_record_count = valid_record_count
        self.quality_report = quality_report
        self.quality_score = quality_score
        self.warnings = warnings


def transform_data(
    raw_file_bytes: bytes,
    file_name: str,
    mappings: list[dict[str, str]],
) -> TransformResult:
    """Transform raw file data using approved field mappings.

    Args:
        raw_file_bytes: Raw file content from Blob Storage.
        file_name: Original filename (for format detection).
        mappings: List of approved mappings, each with
                  {"source_field": "...", "target_feature": "..."}.

    Returns:
        TransformResult with clean CSV, stats, and quality report.
    """
    # 1. Parse raw file
    parsed = parse_file(raw_file_bytes, file_name)

    # 2. Build column rename map: source_field → target_feature
    rename_map: dict[str, str] = {}
    for m in mappings:
        source = m["source_field"].lower().strip()
        target = m["target_feature"].lower().strip()
        if target and target != "_unmapped":
            rename_map[source] = target

    # 3. Build rows with renamed columns
    source_columns = parsed.columns
    target_columns_present: list[str] = []
    source_to_idx: dict[str, int] = {col: i for i, col in enumerate(source_columns)}

    # Determine which target columns we have
    for source_col, target_col in rename_map.items():
        if source_col in source_to_idx:
            target_columns_present.append(target_col)

    # Add target variable if mapped
    has_target_variable = TARGET_VARIABLE in target_columns_present

    # 4. Transform all rows
    output_columns = sorted(set(target_columns_present))
    transformed_rows: list[dict[str, Any]] = []
    type_errors: list[dict[str, Any]] = []
    null_counts: dict[str, int] = {col: 0 for col in output_columns}
    total_rows = 0

    # Build reverse lookup: for each data row, match source cols to target cols
    all_data_rows = _get_all_rows(raw_file_bytes, file_name, parsed)

    for row_idx, row_values in enumerate(all_data_rows):
        if not any(v.strip() for v in row_values if isinstance(v, str)):
            continue  # skip empty rows

        total_rows += 1
        transformed_row: dict[str, Any] = {}

        for source_col, target_col in rename_map.items():
            if target_col == "_unmapped":
                continue
            col_idx = source_to_idx.get(source_col)
            if col_idx is None or col_idx >= len(row_values):
                null_counts[target_col] = null_counts.get(target_col, 0) + 1
                transformed_row[target_col] = None
                continue

            raw_value = row_values[col_idx]
            if raw_value is None or (
                isinstance(raw_value, str) and raw_value.strip() == ""
            ):
                null_counts[target_col] = null_counts.get(target_col, 0) + 1
                transformed_row[target_col] = None
                continue

            # Type casting
            feature_def = TARGET_FEATURES.get(target_col)
            if feature_def:
                casted = _cast_value(
                    raw_value, feature_def["type"], target_col, row_idx
                )
                if casted is None:
                    type_errors.append(
                        {
                            "row": row_idx + 1,
                            "column": target_col,
                            "value": str(raw_value)[:100],
                            "expected_type": feature_def["type"],
                        }
                    )
                transformed_row[target_col] = casted
            else:
                transformed_row[target_col] = raw_value

        # Calculate debt_to_income_ratio if not present but components are
        if (
            "debt_to_income_ratio" not in transformed_row
            or transformed_row.get("debt_to_income_ratio") is None
        ):
            income = transformed_row.get("monthly_income")
            repayment = transformed_row.get("monthly_loan_repayment")
            if income and repayment and float(income) > 0:
                transformed_row["debt_to_income_ratio"] = round(
                    float(repayment) / float(income), 4
                )

        transformed_rows.append(transformed_row)

    # 5. Generate quality report
    valid_rows = [r for r in transformed_rows if any(v is not None for v in r.values())]

    null_percentages = {
        col: round(count / max(total_rows, 1), 4) for col, count in null_counts.items()
    }

    # Check for duplicate rows (hash based)
    seen_hashes: set[int] = set()
    duplicate_count = 0
    for row in valid_rows:
        row_hash = hash(tuple(sorted(row.items())))
        if row_hash in seen_hashes:
            duplicate_count += 1
        seen_hashes.add(row_hash)

    quality_report = {
        "null_percentages": null_percentages,
        "duplicate_rows": duplicate_count,
        "type_errors": type_errors[:50],  # Cap at 50
        "type_error_count": len(type_errors),
        "completeness": round(len(output_columns) / 30, 4),
        "has_target_variable": has_target_variable,
    }

    # Quality score: weighted composite
    avg_nulls = sum(null_percentages.values()) / max(len(null_percentages), 1)
    completeness = len(output_columns) / 30
    error_rate = min(len(type_errors) / max(total_rows, 1), 1.0)
    quality_score = round(
        max(0.0, completeness * 0.4 + (1 - avg_nulls) * 0.3 + (1 - error_rate) * 0.3),
        2,
    )

    # Warnings
    warnings: list[str] = []
    if not has_target_variable:
        warnings.append(
            f"CRITICAL: Target variable '{TARGET_VARIABLE}' not found in mapping. "
            "This data cannot be used for model training without it."
        )
    if len(output_columns) < MIN_FEATURES_MAPPED:
        warnings.append(
            f"Only {len(output_columns)} of 18 features mapped "
            f"(minimum recommended: {MIN_FEATURES_MAPPED})."
        )
    if total_rows < MIN_RECORD_COUNT:
        warnings.append(
            f"Only {total_rows} records (minimum: {MIN_RECORD_COUNT}, "
            f"recommended: {RECOMMENDED_RECORD_COUNT})."
        )
    elif total_rows < RECOMMENDED_RECORD_COUNT:
        warnings.append(
            f"{total_rows} records (recommended: {RECOMMENDED_RECORD_COUNT}+)."
        )
    if duplicate_count > 0:
        warnings.append(f"{duplicate_count} duplicate rows detected.")

    # 6. Write clean CSV
    csv_buffer = io.StringIO()
    writer = csv.DictWriter(
        csv_buffer, fieldnames=output_columns, extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(valid_rows)
    csv_bytes = csv_buffer.getvalue().encode("utf-8")

    return TransformResult(
        csv_bytes=csv_bytes,
        record_count=total_rows,
        valid_record_count=len(valid_rows),
        quality_report=quality_report,
        quality_score=quality_score,
        warnings=warnings,
    )


def _get_all_rows(
    raw_bytes: bytes, file_name: str, parsed_result: Any
) -> list[list[Any]]:
    """Re-parse the full file to get all data rows (not just samples)."""
    full_parse = parse_file(raw_bytes, file_name)
    # For CSV/TSV/TXT, sample_rows might be truncated, so re-parse fully
    # The parse_file already returns all rows via row_count, but sample is limited
    # We need the full data, so we re-parse

    ext = file_name.rsplit(".", 1)[-1].lower()

    if ext in ("csv", "tsv", "txt"):
        import csv as csv_mod

        text = raw_bytes.decode("utf-8-sig")
        delimiter = "\t" if ext == "tsv" else None
        if delimiter:
            reader = csv_mod.reader(io.StringIO(text), delimiter=delimiter)
        else:
            try:
                dialect = csv_mod.Sniffer().sniff(text[:8192])
                reader = csv_mod.reader(io.StringIO(text), dialect)
            except csv_mod.Error:
                reader = csv_mod.reader(io.StringIO(text))

        rows = list(reader)
        return rows[1:]  # skip header

    elif ext == "json":
        import json as json_mod
        from app.services.file_parser import _flatten_dict

        parsed = json_mod.loads(raw_bytes.decode("utf-8-sig"))
        if isinstance(parsed, dict):
            for val in parsed.values():
                if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                    parsed = val
                    break

        flat_records = [_flatten_dict(r) for r in parsed]
        columns = full_parse.columns
        return [
            [str(record.get(col, "")) for col in columns] for record in flat_records
        ]

    elif ext == "xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows: list[list[Any]] = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue  # skip header
            rows.append([str(cell) if cell is not None else "" for cell in row])
        wb.close()
        return rows

    return []


def _cast_value(
    value: Any, target_type: str, col_name: str, row_idx: int
) -> Any | None:
    """Attempt to cast a value to the target type."""
    if value is None:
        return None

    str_val = str(value).strip()
    if str_val == "":
        return None

    try:
        if target_type == "integer":
            # Handle float strings like "35.0"
            return int(float(str_val))
        elif target_type == "float":
            return float(str_val)
        elif target_type == "string":
            return str_val
        else:
            return str_val
    except (ValueError, TypeError):
        logger.debug(
            f"Type cast failed: row={row_idx}, col={col_name}, "
            f"value={str_val!r}, target={target_type}"
        )
        return None
