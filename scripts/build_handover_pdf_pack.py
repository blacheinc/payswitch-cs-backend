#!/usr/bin/env python3
"""Build the client-facing PaySwitch backend handover PDF pack."""

from __future__ import annotations

import hashlib
import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "pdf" / "payswitch-backend-handover"
ZIP_PATH = ROOT / "output" / "pdf" / "PaySwitch_Backend_Handover_Pack_2026-07-04.zip"
DIAGRAM_DIR = ROOT / "tmp" / "pdfs" / "diagrams"
PACKAGE_DATE = "4 July 2026"

NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#23689B")
TEAL = colors.HexColor("#1B7F79")
GOLD = colors.HexColor("#D49A28")
PALE_BLUE = colors.HexColor("#EAF2F8")
PALE_GREY = colors.HexColor("#F4F6F7")
MID_GREY = colors.HexColor("#667788")
WHITE = colors.white


@dataclass(frozen=True)
class DocumentSpec:
    number: int
    source: str
    output: str
    landscape_page: bool = False


DOCUMENTS = [
    DocumentSpec(0, "docs/handover/client-pdf-index.md", "00_Backend_Handover_Document_Index.pdf"),
    DocumentSpec(1, "docs/handover/executive-summary.md", "01_Executive_Handover_Summary.pdf"),
    DocumentSpec(
        2,
        "docs/handover/responsibility-matrix.md",
        "02_Project_Handover_Responsibility_Matrix.pdf",
        True,
    ),
    DocumentSpec(
        3,
        "docs/handover/delivery-acceptance-checklist.md",
        "03_Delivery_Acceptance_Checklist.pdf",
    ),
    DocumentSpec(4, "docs/handover/qa/backend-qa-report.md", "04_Backend_Internal_QA_Report.pdf"),
    DocumentSpec(
        5,
        "docs/handover/qa/verification-evidence.md",
        "05_Backend_Verification_Evidence.pdf",
    ),
    DocumentSpec(
        6,
        "docs/handover/code-review/backend-code-review.md",
        "06_Backend_Code_Review_Update.pdf",
    ),
    DocumentSpec(
        7,
        "docs/handover/privacy/backend-dpia-technical-input.md",
        "07_Backend_DPIA_Technical_Contribution.pdf",
    ),
    DocumentSpec(
        8,
        "docs/handover/security/internal-vapt-report.md",
        "08_Internal_Backend_Application_Security_Assessment.pdf",
    ),
    DocumentSpec(
        9,
        "docs/handover/security/backend-security-controls.md",
        "09_Backend_Security_Controls_Implemented.pdf",
        True,
    ),
    DocumentSpec(
        10,
        "docs/handover/security/security-assurance-register.md",
        "10_Backend_Security_Assurance_Register.pdf",
        True,
    ),
    DocumentSpec(11, "docs/architecture/overview.md", "11_Backend_Architecture_Overview.pdf"),
    DocumentSpec(12, "docs/architecture/data-flows.md", "12_Backend_Data_Flows.pdf"),
    DocumentSpec(13, "docs/architecture/data-model.md", "13_Backend_Data_Model.pdf"),
    DocumentSpec(14, "docs/architecture/security.md", "14_Backend_Security_Architecture.pdf"),
]


def styles():
    sample = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=sample["Title"],
            fontName="Helvetica-Bold",
            fontSize=27,
            leading=32,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=10 * mm,
        ),
        "cover_brand": ParagraphStyle(
            "CoverBrand",
            parent=sample["Normal"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=17,
            textColor=TEAL,
            spaceAfter=4 * mm,
        ),
        "cover_meta": ParagraphStyle(
            "CoverMeta",
            parent=sample["Normal"],
            fontSize=10.5,
            leading=16,
            textColor=MID_GREY,
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=sample["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=NAVY,
            spaceBefore=4 * mm,
            spaceAfter=3 * mm,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=sample["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13.5,
            leading=17,
            textColor=BLUE,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=sample["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=TEAL,
            spaceBefore=3 * mm,
            spaceAfter=1.5 * mm,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=13.2,
            textColor=colors.HexColor("#263746"),
            spaceAfter=2.4 * mm,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=9.1,
            leading=12.7,
            leftIndent=5 * mm,
            firstLineIndent=-3.5 * mm,
            bulletIndent=1 * mm,
            textColor=colors.HexColor("#263746"),
            spaceAfter=1.3 * mm,
        ),
        "code": ParagraphStyle(
            "Code",
            parent=sample["Code"],
            fontName="Courier",
            fontSize=7.3,
            leading=9.5,
            leftIndent=3 * mm,
            rightIndent=3 * mm,
            backColor=PALE_GREY,
            borderColor=colors.HexColor("#D9E1E8"),
            borderWidth=0.5,
            borderPadding=5,
            spaceBefore=1.5 * mm,
            spaceAfter=3 * mm,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=sample["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9.3,
            textColor=WHITE,
        ),
        "table_cell": ParagraphStyle(
            "TableCell",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=7.2,
            leading=9.2,
            textColor=colors.HexColor("#263746"),
        ),
        "caption": ParagraphStyle(
            "Caption",
            parent=sample["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=7.8,
            leading=10,
            textColor=MID_GREY,
            alignment=TA_CENTER,
            spaceAfter=3 * mm,
        ),
    }


def inline_markup(value: str) -> str:
    tokens: dict[str, str] = {}

    def token(markup: str) -> str:
        key = f"@@TOKEN{len(tokens)}@@"
        tokens[key] = markup
        return key

    value = re.sub(
        r"\[([^]]+)\]\(([^)]+)\)",
        lambda match: token(
            f'<link href="{html.escape(match.group(2), quote=True)}" color="#23689B">'
            f"{html.escape(match.group(1))}</link>"
        ),
        value,
    )
    value = re.sub(
        r"`([^`]+)`",
        lambda match: token(f'<font name="Courier">{html.escape(match.group(1))}</font>'),
        value,
    )
    value = html.escape(value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", value)
    for key, markup in tokens.items():
        value = value.replace(key, markup)
    return value


def extract_title(lines: list[str], fallback: str) -> tuple[str, list[str]]:
    for index, line in enumerate(lines):
        if line.startswith("# "):
            return line[2:].strip(), lines[:index] + lines[index + 1 :]
    return fallback, lines


def add_table(story: list, rows: list[list[str]], available_width: float, style_map: dict) -> None:
    if not rows:
        return
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    data = []
    for row_index, row in enumerate(normalized):
        cell_style = style_map["table_header"] if row_index == 0 else style_map["table_cell"]
        data.append([Paragraph(inline_markup(cell), cell_style) for cell in row])
    widths = [available_width / column_count] * column_count
    vertical_padding = 1.0 if len(rows) > 20 else 2.2
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C9D4DD")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, PALE_BLUE]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), vertical_padding),
                ("BOTTOMPADDING", (0, 0), (-1, -1), vertical_padding),
            ]
        )
    )
    story.extend([table, Spacer(1, 3 * mm)])


DIAGRAM_NODES = {
    "architecture": [
        ("Clients", "Dashboard + B2B API"),
        ("FastAPI backend", "HTTP + batch workers"),
        ("Data services", "Postgres, Redis, Bus, Blob"),
        ("Integrations", "Bureau, ML, email, logs"),
    ],
    "scoring-flow": [
        ("Client", "Submit application"),
        ("Backend API", "Validate + track"),
        ("XDS Bureau", "Retrieve report"),
        ("Decision flow", "Hard stops + ML"),
        ("Result", "Persist + webhook"),
    ],
    "batch-flow": [
        ("Client", "Submit batch"),
        ("Batch job", "Persist item rows"),
        ("Redis queue", "Distribute work"),
        ("Workers", "Bureau + scoring"),
        ("Results", "Isolated item outcomes"),
    ],
    "rbac-flow": [
        ("Caller", "JWT or API key"),
        ("AuthContext", "Resolve identity"),
        ("Rate limit", "Organisation quota"),
        ("RBAC", "Resolve permission"),
        ("Endpoint", "Serve or deny + audit"),
    ],
    "er-diagram": [
        ("Identity", "Users, keys, roles"),
        ("Organisation", "Tenant boundary"),
        ("Scoring", "Requests + results"),
        ("Batch", "Jobs + item state"),
        ("Assurance", "Audit + monitoring + ML"),
    ],
}


def vector_diagram(source: Path, available_width: float) -> Drawing:
    nodes = DIAGRAM_NODES.get(source.stem, [("System flow", source.stem.replace("-", " "))])
    height = 72
    drawing = Drawing(available_width, height)
    gap = 12
    node_width = (available_width - gap * (len(nodes) - 1)) / len(nodes)
    node_height = 48
    y = 15
    for index, (title, subtitle) in enumerate(nodes):
        x = index * (node_width + gap)
        fill = PALE_BLUE if index % 2 == 0 else colors.HexColor("#EAF7F5")
        drawing.add(
            Rect(
                x,
                y,
                node_width,
                node_height,
                rx=5,
                ry=5,
                fillColor=fill,
                strokeColor=TEAL,
                strokeWidth=0.8,
            )
        )
        drawing.add(
            String(
                x + node_width / 2,
                y + 29,
                title,
                textAnchor="middle",
                fontName="Helvetica-Bold",
                fontSize=7.8,
                fillColor=NAVY,
            )
        )
        drawing.add(
            String(
                x + node_width / 2,
                y + 15,
                subtitle,
                textAnchor="middle",
                fontName="Helvetica",
                fontSize=6.2,
                fillColor=MID_GREY,
            )
        )
        if index < len(nodes) - 1:
            start = x + node_width + 2
            end = x + node_width + gap - 3
            midpoint = y + node_height / 2
            drawing.add(Line(start, midpoint, end, midpoint, strokeColor=GOLD, strokeWidth=1.2))
            drawing.add(
                Polygon(
                    [end, midpoint, end - 4, midpoint + 2.5, end - 4, midpoint - 2.5],
                    fillColor=GOLD,
                    strokeColor=GOLD,
                )
            )
    return drawing


def diagram_flowable(
    source: Path,
    available_width: float,
    available_height: float,
    style_map: dict,
):
    rendered = DIAGRAM_DIR / f"{source.stem}.png"
    if not rendered.exists():
        return [
            vector_diagram(source, available_width),
            Paragraph(
                f"Architecture flow - source: "
                f"<font name='Courier'>{html.escape(source.name)}</font>",
                style_map["caption"],
            ),
        ]
    image = Image(str(rendered))
    image._restrictSize(available_width, available_height)
    return [image, Paragraph(source.stem.replace("-", " ").title(), style_map["caption"])]


def markdown_story(
    source: Path,
    page_width: float,
    page_height: float,
    style_map: dict,
) -> tuple[str, list]:
    lines = source.read_text(encoding="utf-8").splitlines()
    title, lines = extract_title(lines, source.stem.replace("-", " ").title())
    story: list = []
    index = 0
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_lines:
            story.append(Paragraph(inline_markup(" ".join(paragraph_lines)), style_map["body"]))
            paragraph_lines.clear()

    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            index += 1
            continue
        if stripped.startswith("```"):
            flush_paragraph()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index].rstrip())
                index += 1
            story.append(Preformatted("\n".join(code_lines), style_map["code"], maxLineLength=105))
            index += 1
            continue
        if stripped.startswith("| ") or (stripped.startswith("|") and stripped.endswith("|")):
            flush_paragraph()
            rows: list[list[str]] = []
            while index < len(lines):
                candidate = lines[index].strip()
                if not (candidate.startswith("|") and candidate.endswith("|")):
                    break
                cells = [cell.strip() for cell in candidate.strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    rows.append(cells)
                index += 1
            add_table(story, rows, page_width, style_map)
            continue
        image_match = re.fullmatch(r"!\[([^]]*)\]\(([^)]+)\)", stripped)
        if image_match:
            flush_paragraph()
            target = (source.parent / image_match.group(2)).resolve()
            if target.suffix == ".mmd":
                story.extend(diagram_flowable(target, page_width, page_height * 0.55, style_map))
            index += 1
            continue
        heading = re.match(r"^(#{2,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            level = min(len(heading.group(1)) - 1, 3)
            story.append(Paragraph(inline_markup(heading.group(2)), style_map[f"h{level}"]))
            index += 1
            continue
        if re.fullmatch(r"-{3,}", stripped):
            flush_paragraph()
            story.extend(
                [
                    Spacer(1, 1 * mm),
                    HRFlowable(color=GOLD, thickness=0.8),
                    Spacer(1, 2 * mm),
                ]
            )
            index += 1
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if bullet or numbered:
            flush_paragraph()
            if bullet:
                content = bullet.group(1).replace("[x]", "Completed:").replace("[ ]", "Pending:")
                marker = "•"
            else:
                content = numbered.group(2)
                marker = f"{numbered.group(1)}."
            story.append(Paragraph(inline_markup(content), style_map["bullet"], bulletText=marker))
            index += 1
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            quote = stripped.lstrip("> ")
            quote_style = ParagraphStyle(
                "Quote",
                parent=style_map["body"],
                leftIndent=5 * mm,
                borderColor=TEAL,
                borderWidth=0,
                borderPadding=5,
                backColor=PALE_BLUE,
                textColor=NAVY,
            )
            story.append(Paragraph(inline_markup(quote), quote_style))
            index += 1
            continue
        paragraph_lines.append(stripped)
        index += 1
    flush_paragraph()
    return title, story


def page_decorator(canvas, doc) -> None:
    canvas.saveState()
    width, height = doc.pagesize
    if doc.page > 1:
        canvas.setFillColor(NAVY)
        canvas.rect(0, height - 14 * mm, width, 14 * mm, fill=1, stroke=0)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawString(doc.leftMargin, height - 9 * mm, "PaySwitch Credit Scoring Platform")
        canvas.setFont("Helvetica", 7.5)
        canvas.drawRightString(width - doc.rightMargin, height - 9 * mm, "Backend Handover")
    canvas.setStrokeColor(colors.HexColor("#D8E0E6"))
    canvas.line(doc.leftMargin, 13 * mm, width - doc.rightMargin, 13 * mm)
    canvas.setFillColor(MID_GREY)
    canvas.setFont("Helvetica", 7.2)
    canvas.drawString(doc.leftMargin, 8 * mm, f"Client handover | {PACKAGE_DATE}")
    canvas.drawRightString(width - doc.rightMargin, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_document(spec: DocumentSpec) -> Path:
    source = ROOT / spec.source
    destination = OUTPUT_DIR / spec.output
    page_size = landscape(A4) if spec.landscape_page else A4
    document = SimpleDocTemplate(
        str(destination),
        pagesize=page_size,
        leftMargin=17 * mm,
        rightMargin=17 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=source.stem,
        author="PaySwitch Backend Engineering",
        subject="PaySwitch backend client handover",
    )
    style_map = styles()
    available_width = page_size[0] - document.leftMargin - document.rightMargin
    available_height = page_size[1] - document.topMargin - document.bottomMargin
    title, content = markdown_story(source, available_width, available_height, style_map)
    story = [
        Spacer(1, 25 * mm),
        Paragraph("PAYSWITCH CREDIT SCORING PLATFORM", style_map["cover_brand"]),
        HRFlowable(width="35%", thickness=3, color=GOLD, hAlign="LEFT"),
        Spacer(1, 12 * mm),
        Paragraph(inline_markup(title), style_map["cover_title"]),
        Spacer(1, 6 * mm),
        Paragraph(
            "Backend Handover Package<br/>"
            f"Document {spec.number:02d} | {PACKAGE_DATE}<br/>"
            "Prepared by Backend Engineering<br/><br/>"
            "Classification: Client handover",
            style_map["cover_meta"],
        ),
        Spacer(1, 20 * mm),
        Table(
            [[Paragraph("Evidence-based technical delivery documentation", style_map["body"])]],
            colWidths=[available_width * 0.7],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
                    ("BOX", (0, 0), (-1, -1), 0.8, TEAL),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            ),
        ),
        PageBreak(),
        Paragraph(inline_markup(title), style_map["h1"]),
        HRFlowable(color=GOLD, thickness=1.2),
        Spacer(1, 4 * mm),
        *content,
    ]
    document.build(story, onFirstPage=page_decorator, onLaterPages=page_decorator)
    return destination


def build_zip(pdf_paths: list[Path]) -> None:
    checksums = []
    for path in pdf_paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksums.append(f"{digest}  {path.name}")
    checksum_path = OUTPUT_DIR / "SHA256SUMS.txt"
    checksum_path.write_text("\n".join(checksums) + "\n", encoding="utf-8")
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in pdf_paths:
            archive.write(path, arcname=f"PaySwitch_Backend_Handover/{path.name}")
        archive.write(checksum_path, arcname="PaySwitch_Backend_Handover/SHA256SUMS.txt")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for existing in OUTPUT_DIR.glob("*.pdf"):
        existing.unlink()
    pdf_paths = [build_document(spec) for spec in DOCUMENTS]
    build_zip(pdf_paths)
    print(f"Built {len(pdf_paths)} PDFs")
    print(ZIP_PATH)


if __name__ == "__main__":
    main()
