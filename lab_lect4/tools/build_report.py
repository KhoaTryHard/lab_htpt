"""Build a two-page Vietnamese PDF from editable Markdown and saved evidence."""
import argparse
import json
from pathlib import Path
import re
from statistics import mean
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (PageBreak, Paragraph, Preformatted,
                               SimpleDocTemplate, Spacer)
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def find_fonts(font_directory):
    candidates = [Path(font_directory)] if font_directory else [
        Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")]
    for directory in candidates:
        for regular, bold in (("arial.ttf", "arialbd.ttf"),
                              ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")):
            if (directory / regular).exists() and (directory / bold).exists():
                return directory / regular, directory / bold
    raise RuntimeError("Vietnamese font not found; specify --font-dir with Arial or DejaVu Sans")


def evidence_values():
    tests = json.loads((ROOT / "logs/verification/summary.json").read_text(encoding="utf-8"))
    stress = json.loads((ROOT / "logs/stress/summary.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (ROOT / "logs/trace.jsonl").read_text(encoding="utf-8").splitlines()]
    passed = next(record for record in records if record["event"] == "PASS")
    if not tests["passed"] or stress["passed"] != stress["runs"]:
        raise RuntimeError("Verification must pass before publishing the report")
    durations = [result["failover_seconds"] for result in stress["results"]]
    text = (f"Python {tests['python']}: {tests['tests']}/{tests['tests']} unittest qua, "
            f"không lỗi hoặc thất bại ({tests['seconds']:.2f} giây). "
            f"Chạy lặp cấu hình mặc định: {stress['passed']}/{stress['runs']} PASS. "
            f"Thời gian từ CRASH tới hội tụ và hết các vòng: trung bình {mean(durations):.3f} giây, "
            f"min {min(durations):.3f}, max {max(durations):.3f}; sau đó theo dõi ổn định thêm hai giây. "
            f"Demo bàn giao PASS, P1-P4 đều nhận leader 4; có {passed['completed_rounds']} vòng bầu lại hoàn tất. "
            "Bằng chứng: logs/trace.*, logs/verification/summary.json, logs/stress/summary.json.")
    crash = next(record for record in records if record["event"] == "CRASH")
    timeout = next(record for record in records if record["event"] == "FAILURE_TIMEOUT")
    bypass = next(record for record in records if record["event"] == "BYPASS")
    collected = next(record for record in records if record["event"] == "ELECTION_COLLECTED" and record["leader_id"] == 4)
    origin, counter = collected["election_id"]
    completed = next(record for record in records if record["event"] == "ROUND_COMPLETE" and
                     record["election_id"] == collected["election_id"])
    trace = "```text\n" + "\n".join([
        f"{crash['elapsed']:6.3f}s [SYSTEM] P5 terminated (thread joined)",
        f"{timeout['elapsed']:6.3f}s [P{timeout['node']}] FAILURE_TIMEOUT: P5",
        f"{bypass['elapsed']:6.3f}s [P4] BYPASS: P5 unreachable -> P1",
        f"{collected['elapsed']:6.3f}s [P{origin}] round=P{origin}:{counter} ids={collected['participants']}",
        f"{completed['elapsed']:6.3f}s [P{origin}] COORDINATOR returned; leader=P4",
        f"{passed['elapsed']:6.3f}s [SYSTEM] PASS: P1-P4 -> leader P4"
    ]) + "\n```"
    return text, trace


def make_pdf(source, destination):
    body = ParagraphStyle("Body", fontName="Vietnamese", fontSize=9.6, leading=13.2,
                          textColor=colors.HexColor("#17212b"), spaceAfter=6, alignment=TA_LEFT)
    heading = ParagraphStyle("Heading", parent=body, fontName="VietnameseBold", fontSize=11,
                             leading=15, spaceBefore=8, spaceAfter=5,
                             textColor=colors.HexColor("#194d66"))
    title = ParagraphStyle("Title", parent=heading, fontSize=19, leading=25,
                           spaceBefore=0, spaceAfter=6)
    code = ParagraphStyle("Code", fontName="Courier", fontSize=8.1, leading=11,
                          backColor=colors.HexColor("#eff4f6"), borderPadding=7,
                          spaceBefore=4, spaceAfter=8)
    reference = ParagraphStyle("Reference", parent=body, fontSize=8.1, leading=10.6,
                               spaceAfter=3)
    blocks = re.split(r"\n\s*\n", source.strip())
    story = []
    in_references = False
    for block in blocks:
        if block == "<!-- PAGEBREAK -->":
            story.append(PageBreak())
        elif block.startswith("```text\n"):
            text = block.removeprefix("```text\n").removesuffix("\n```")
            story.append(Preformatted(text, code))
        elif block.startswith("# "):
            lines = block.splitlines()
            story.append(Paragraph(escape(lines[0][2:]), title))
            if len(lines) > 1:
                story.append(Paragraph(escape(" ".join(lines[1:])), body))
            story.append(Spacer(1, 4))
        elif block.startswith("## "):
            lines = block.splitlines()
            in_references = lines[0][3:] == "Tài liệu tham khảo"
            story.append(Paragraph(escape(lines[0][3:]), heading))
            if len(lines) > 1:
                if in_references:
                    for line in lines[1:]:
                        story.append(Paragraph(escape(line), reference))
                else:
                    story.append(Paragraph(escape(" ".join(lines[1:])), body))
        else:
            if in_references:
                for line in block.splitlines():
                    story.append(Paragraph(escape(line), reference))
            else:
                story.append(Paragraph(escape(" ".join(block.splitlines())), body))

    def page_decoration(canvas, document):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#afc2cb"))
        canvas.line(18 * mm, 16 * mm, width - 18 * mm, 16 * mm)
        canvas.setFont("Vietnamese", 8)
        canvas.setFillColor(colors.HexColor("#536773"))
        canvas.drawString(18 * mm, 11 * mm, "Lab 04 | Ring Leader Election | Python + TCP + Threads")
        canvas.drawRightString(width - 18 * mm, 11 * mm, f"{document.page} / 2")
        if document.page > 1:
            canvas.setFont("VietnameseBold", 9)
            canvas.drawString(18 * mm, height - 13 * mm, "BÁO CÁO KIẾN TRÚC / RING LEADER ELECTION")
        canvas.restoreState()

    document = SimpleDocTemplate(str(destination), pagesize=A4,
                                 rightMargin=18 * mm, leftMargin=18 * mm,
                                 topMargin=19 * mm, bottomMargin=21 * mm,
                                 title="Báo cáo kiến trúc Ring Leader Election",
                                 author="Distributed Systems Lab", allowSplitting=0)
    document.build(story, onFirstPage=page_decoration, onLaterPages=page_decoration)
    reader = PdfReader(destination)
    if len(reader.pages) != 2:
        raise RuntimeError(f"Report must have exactly two pages, got {len(reader.pages)}")
    if any("{{" in (page.extract_text() or "") for page in reader.pages):
        raise RuntimeError("Unresolved evidence placeholder in PDF")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font-dir")
    args = parser.parse_args()
    regular, bold = find_fonts(args.font_dir)
    pdfmetrics.registerFont(TTFont("Vietnamese", str(regular)))
    pdfmetrics.registerFont(TTFont("VietnameseBold", str(bold)))
    source = (ROOT / "docs/bao_cao_kien_truc.md").read_text(encoding="utf-8")
    evidence, trace = evidence_values()
    resolved = source.replace("{{evidence}}", evidence).replace("{{trace}}", trace)
    output = ROOT / "output/pdf"
    output.mkdir(parents=True, exist_ok=True)
    (output / "bao_cao_kien_truc.md").write_text(resolved, encoding="utf-8")
    destination = output / "bao_cao_kien_truc_ring.pdf"
    make_pdf(resolved, destination)
    print(f"Created {destination} (2 pages)")


if __name__ == "__main__":
    main()
