"""
R Assignment Auto-Grader — BUAN 6356 Pinnacle Outdoor Co.
===================================================
- Automatically grades .R / .Rmd / .ipynb files placed in the submissions/ folder
- Solution-based logic grading (base R, tidyverse, and data.table all supported)
- Automatically extracts written responses from comments, Rmd markdown, and notebook markdown cells
- Output: grading_results/{student_name}_feedback.txt + grading_report.csv

Installation:  pip install anthropic watchdog python-dotenv
Run:  python auto_grader.py
Single file:  python auto_grader.py --grade-file submissions/student1.ipynb
"""

import os, sys, re, csv, json, time, logging, argparse
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import anthropic

# ──────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────
CONFIG = {
    "watch_folder":       "./submissions",
    "output_folder":      "./grading_results",
    "rubric_file":        "./rubric.json",
    "solution_file":      "./solution.ipynb",
    "allowed_extensions": [".r", ".rmd", ".ipynb"],
    "api_key": os.environ.get("ANTHROPIC_API_KEY", "YOUR_API_KEY_HERE"),
}
# ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# 1. File Parser
# ══════════════════════════════════════════════════════

def parse_file(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".ipynb":
        return _parse_ipynb(raw)
    if suffix == ".rmd":
        return _parse_rmd(raw)
    return _parse_r(raw)


def _parse_ipynb(raw: str) -> dict:
    try:
        nb = json.loads(raw)
    except json.JSONDecodeError:
        return {"code": "", "narrative": "", "raw": raw}
    code_blocks, narrative_parts = [], []
    for cell in nb.get("cells", []):
        source = "".join(cell.get("source", [])).replace("\r\n", "\n").replace("\r", "\n")
        if cell.get("cell_type") == "code":
            code_blocks.append(source)
        elif cell.get("cell_type") == "markdown":
            narrative_parts.append(source)
    return {
        "code": "\n\n".join(code_blocks).strip(),
        "narrative": "\n\n".join(narrative_parts).strip(),
        "raw": raw,
    }


def _parse_r(raw: str) -> dict:
    code_lines, narrative_lines = [], []
    for line in raw.splitlines():
        s = line.strip()
        if not s or re.match(r"^#+[-=\s]*$", s):
            code_lines.append(line)
            continue
        if s.startswith("#"):
            body = s.lstrip("#").strip()
            if len(body) >= 20 and re.search(r"[가-힣]|[.?!,]", body):
                narrative_lines.append(body)
            else:
                code_lines.append(line)
        else:
            code_lines.append(line)
    return {
        "code": "\n".join(code_lines).strip(),
        "narrative": "\n".join(narrative_lines).strip(),
        "raw": raw,
    }


def _parse_rmd(raw: str) -> dict:
    code_blocks, narrative_parts = [], []
    in_chunk, current_chunk = False, []
    for line in raw.splitlines():
        if re.match(r"^```\{r", line, re.IGNORECASE):
            in_chunk, current_chunk = True, []
        elif line.strip() == "```" and in_chunk:
            in_chunk = False
            code_blocks.append("\n".join(current_chunk))
        elif in_chunk:
            current_chunk.append(line)
        elif not line.startswith("---"):
            narrative_parts.append(line)
    return {
        "code": "\n\n".join(code_blocks).strip(),
        "narrative": "\n".join(narrative_parts).strip(),
        "raw": raw,
    }


# ══════════════════════════════════════════════════════
# 2. Solution Loader
# ══════════════════════════════════════════════════════

def load_solution() -> str:
    sol_path = Path(CONFIG["solution_file"])
    if not sol_path.exists():
        log.warning(f"Solution file not found ({sol_path}) — Proceeding with logic-based grading only.")
        return ""
    if sol_path.suffix.lower() == ".ipynb":
        nb = json.loads(sol_path.read_text(encoding="utf-8"))
        lines = []
        for cell in nb.get("cells", []):
            src = "".join(cell.get("source", []))
            if cell.get("cell_type") == "code":
                lines.append("[Code]\n" + src)
            elif cell.get("cell_type") == "markdown":
                lines.append("[Explanation]\n" + src)
        return "\n\n".join(lines)
    return sol_path.read_text(encoding="utf-8", errors="replace")


def load_rubric() -> dict:
    rubric_path = Path(CONFIG["rubric_file"])
    if not rubric_path.exists():
        log.error(f"Rubric not found: {rubric_path}")
        sys.exit(1)
    return json.loads(rubric_path.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════
# 3. Grading Engine
# ══════════════════════════════════════════════════════

def _rubric_to_prompt(rubric: dict) -> str:
    lines = [f"Total Points: {rubric.get('total_points', 100)} pts"]
    for part in rubric.get("parts", []):
        lines.append(f"\n[{part['name']} — {part['points']} pts]")
        for item in part.get("items", []):
            lines.append(f"  {item['id']}. {item['name']} ({item['points']} pts) [{item['type']}]")
            lines.append(f"     Full Credit: {item['full_credit']}")
            if item.get("partial_credit"):
                lines.append(f"     Partial Credit: {item['partial_credit']}")
            lines.append(f"     No Credit: {item['no_credit']}")
            if item.get("grading_note"):
                lines.append(f"     Note: {item['grading_note']}")
    return "\n".join(lines)


def grade_submission(file_path: Path, rubric: dict, solution: str) -> dict:
    client = anthropic.Anthropic(api_key=CONFIG["api_key"])
    parsed = parse_file(file_path)

    rubric_text    = _rubric_to_prompt(rubric)
    total_possible = rubric.get("total_points", 100)

    sol_excerpt = solution[:5000] + "\n...(omitted)" if len(solution) > 5000 else solution
    sol_section = f"\n\n[Solution Key — For Logic Comparison]\n{sol_excerpt}" if sol_excerpt else ""

    # Specify the part list in the prompt so Claude returns exactly the same key names
    parts_schema = []
    for part in rubric.get("parts", []):
        item_schema = []
        for item in part.get("items", []):
            item_schema.append(
                f'{{"item_id":"{item["id"]}","name":"{item["name"]}",'
                f'"earned":<score>,"max":{item["points"]},"verdict":"full/partial/none","reason":"justification"}}'
            )
        parts_schema.append(
            f'{{"part_id":"{part["id"]}","part_name":"{part["name"]}",'
            f'"earned":<score>,"max":{part["points"]},"items":[{",".join(item_schema)}]}}'
        )

    system = f"""You are {rubric.get('course','BUAN 6356')} assignment evaluator.

[Grading Principles]
{rubric.get('grading_philosophy','')}
- Award full points if the logic is correct, regardless of the coding style.
- Grade narrative responses based on content accuracy and logical soundness.
- Actively award partial credit where appropriate.

You must output valid, raw JSON only matching the schema below (do not wrap in markdown):
{{
  "total": <integer from 0 to {total_possible}>,
  "grade": "<A/B/C/D/F>",
  "part_scores": [
    {chr(10).join(parts_schema)}
  ],
  "code_feedback": "<2-3 sentences of coding feedback>",
  "narrative_feedback": "<2-3 sentences of narrative response feedback>",
  "overall_feedback": "<3-4 sentences of overall feedback>"
}}"""

    user = f"""Assignment: {rubric.get('assignment','Pinnacle Outdoor Co. Case Study')}
{sol_section}

[Rubric]
{rubric_text}

[Student File: {file_path.name}]

── Code ──
{parsed['code'][:7000] or '[No Code]'}
{'...(omitted)' if len(parsed['code']) > 7000 else ''}

── Narrative Responses ──
{parsed['narrative'][:3000] or '[No Narrative Responses]'}
{'...(omitted)' if len(parsed['narrative']) > 3000 else ''}

Respond in JSON format only."""

    # API Call — Retry once on failure
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                temperature=0,
                max_tokens=8000,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            raw = response.content[0].text.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            result = json.loads(raw)
            break
        except json.JSONDecodeError as e:
            if attempt == 0:
                log.warning(f"JSON parsing failed, retrying... ({e})")
                time.sleep(2)
            else:
                raise RuntimeError(f"JSON parsing failed after 2 attempts: {e}\nResponse prefix: {raw[:200]}")

    result["file"]                  = file_path.name
    result["graded_at"]             = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["parsed_code_lines"]     = len(parsed["code"].splitlines())
    result["parsed_narrative_chars"]= len(parsed["narrative"])
    return result


# ══════════════════════════════════════════════════════
# 4. Save Results
# ══════════════════════════════════════════════════════

def save_results(result: dict, output_dir: Path, rubric: dict):
    stem = Path(result["file"]).stem
    verdict_label = {"full": "Full", "partial": "Partial", "none": "None"}

    # Feedback Text File
    feedback_path = output_dir / f"{stem}_feedback.txt"
    lines = [
        "═" * 50,
        f"  Grading Results for {rubric.get('assignment','Assignment')}",
        "═" * 50,
        f"  File:         {result['file']}",
        f"  Graded At:    {result['graded_at']}",
        f"  Total Score:  {result['total']} pts / {rubric.get('total_points',100)} pts  ({result.get('grade','-')})",
        f"  ({result.get('parsed_code_lines','?')} code lines / {result.get('parsed_narrative_chars','?')} narrative chars)",
        "",
    ]
    for part in result.get("part_scores", []):
        lines.append(f"┌─ {part['part_name']}: {part['earned']}/{part['max']} pts")
        for item in part.get("items", []):
            v   = verdict_label.get(item.get("verdict", ""), "?")
            sym = "✓" if item["verdict"] == "full" else ("△" if item["verdict"] == "partial" else "✗")
            lines.append(f"│  {sym} {item['name']}: {item['earned']}/{item['max']} pts [{v}]")
            if item.get("reason"):
                lines.append(f"│     → {item['reason']}")
        lines.append("│")

    lines += [
        "", "[ Coding Feedback ]",    result.get("code_feedback", ""),
        "", "[ Narrative Feedback ]", result.get("narrative_feedback", ""),
        "", "[ Overall Feedback ]",   result.get("overall_feedback", ""), "",
    ]
    feedback_path.write_text("\n".join(lines), encoding="utf-8")

    # CSV Report — part_scores key is "part_id" (based on Claude response)
    csv_path    = output_dir / "grading_report.csv"
    write_header = not csv_path.exists()

    rubric_parts     = rubric.get("parts", [])
    rubric_part_ids  = [p["id"] for p in rubric_parts]

    # Map from part_id in part_scores
    score_map = {p["part_id"]: p["earned"] for p in result.get("part_scores", [])}
    max_map   = {p["part_id"]: p["max"]    for p in result.get("part_scores", [])}

    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if write_header:
            headers = ["Filename", "Total_Score", "Max_Score", "Grade", "Graded_At"]
            for pid in rubric_part_ids:
                part = next((p for p in rubric_parts if p["id"] == pid), {})
                headers += [f"{part.get('name', pid)}_Score", f"{part.get('name', pid)}_Max"]
            headers.append("Overall_Feedback")
            writer.writerow(headers)

        row = [result["file"], result["total"], rubric.get("total_points", 100),
               result.get("grade", "-"), result["graded_at"]]
        for pid in rubric_part_ids:
            row += [score_map.get(pid, 0), max_map.get(pid, 0)]
        row.append(result.get("overall_feedback", "")[:100] + "…")
        writer.writerow(row)

    log.info(f"  Saved successfully → {feedback_path.name}")


# ══════════════════════════════════════════════════════
# 5. Folder Monitoring
# ══════════════════════════════════════════════════════

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_OK = True
except ImportError:
    WATCHDOG_OK = False


class SubmissionHandler(FileSystemEventHandler if WATCHDOG_OK else object):
    def __init__(self, output_dir, rubric, solution):
        self.output_dir = output_dir
        self.rubric     = rubric
        self.solution   = solution
        self.processing = set()

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in CONFIG["allowed_extensions"]:
            return
        if str(path) in self.processing:
            return
        self.processing.add(str(path))
        time.sleep(1.0)
        log.info(f"New file detected: {path.name}")
        try:
            result = grade_submission(path, self.rubric, self.solution)
            save_results(result, self.output_dir, self.rubric)
            log.info(f"✅ Completed: {path.name} → {result['total']} pts ({result.get('grade','-')})")
        except Exception as e:
            log.error(f"❌ Failed ({path.name}): {e}")
        finally:
            self.processing.discard(str(path))


def grade_existing_files(watch_dir, output_dir, rubric, solution):
    """Grades all existing files in the submissions/ folder on startup."""
    allowed = CONFIG["allowed_extensions"]
    existing = [
        f for f in watch_dir.iterdir()
        if f.is_file() and f.suffix.lower() in allowed
    ]
    if not existing:
        log.info("  No existing files found — starting new file monitoring")
        return

    # Skip files that have already been graded
    to_grade, skipped = [], []
    for f in sorted(existing):
        feedback = output_dir / f"{f.stem}_feedback.txt"
        if feedback.exists():
            skipped.append(f.name)
        else:
            to_grade.append(f)

    if skipped:
        log.info(f"  Skipped {len(skipped)} already graded file(s): {', '.join(skipped)}")
    if not to_grade:
        log.info("  All existing files graded — starting new file monitoring")
        return

    log.info(f"  Starting grading for {len(to_grade)} existing file(s)...")
    for i, path in enumerate(to_grade, 1):
        log.info(f"  [{i}/{len(to_grade)}] {path.name}")
        try:
            result = grade_submission(path, rubric, solution)
            save_results(result, output_dir, rubric)
            log.info(f"  ✅ {path.name} → {result['total']} pts ({result.get('grade','-')})")
        except Exception as e:
            log.error(f"  ❌ {path.name} failed: {e}")


def watch_folder(rubric, solution):
    if not WATCHDOG_OK:
        log.error("watchdog not installed. Run: pip install watchdog")
        sys.exit(1)

    watch_dir  = Path(CONFIG["watch_folder"])
    output_dir = Path(CONFIG["output_folder"])
    watch_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 55)
    log.info("R/ipynb Assignment Auto-Grading System Started")
    log.info(f"  Assignment:   {rubric.get('assignment','?')}")
    log.info(f"  Watch Folder: {watch_dir.resolve()}")
    log.info(f"  Output Folder:{output_dir.resolve()}")
    log.info(f"  Solution Key: {'Available' if solution else 'Not Available'}")
    log.info("=" * 55)

    # Step 1: Grade existing files (skip already graded ones)
    grade_existing_files(watch_dir, output_dir, rubric, solution)

    # Step 2: Monitor for new files
    log.info("  Monitoring for new files... (Drop files into submissions/ to auto-grade)")
    log.info("  Exit: Ctrl+C")

    handler  = SubmissionHandler(output_dir, rubric, solution)
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        log.info("Grading system stopped")
    observer.join()


# ══════════════════════════════════════════════════════
# 6. Entry Point
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="R/ipynb Assignment Auto-Grader")
    parser.add_argument("--grade-file", metavar="FILE", help="Grade a single file immediately")
    args = parser.parse_args()

    rubric   = load_rubric()
    solution = load_solution()

    if args.grade_file:
        path = Path(args.grade_file)
        log.info(f"Single Grading Mode: {path.name}")
        try:
            result = grade_submission(path, rubric, solution)
            out = Path(CONFIG["output_folder"])
            out.mkdir(parents=True, exist_ok=True)
            save_results(result, out, rubric)
            log.info(f"✅ Completed: {result['total']} pts ({result.get('grade','-')})")
        except Exception as e:
            log.error(f"❌ Grading Failed: {e}")
            sys.exit(1)
    else:
        watch_folder(rubric, solution)
