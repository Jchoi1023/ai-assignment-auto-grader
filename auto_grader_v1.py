"""
R 과제 자동채점 — BUAN 6356 Pinnacle Outdoor Co.
===================================================
- .R / .Rmd / .ipynb 파일을 submissions/ 폴더에 넣으면 자동 채점
- 정답지 기반 로직 채점 (base R / tidyverse / data.table 모두 허용)
- # 코멘트 / Rmd 마크다운 / ipynb 마크다운 셀에서 서술형 자동 추출
- 결과: grading_results/{학생명}_feedback.txt + grading_report.csv

설치:  pip install anthropic watchdog python-dotenv
실행:  python auto_grader.py
단건:  python auto_grader.py --grade-file submissions/student1.ipynb
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
# 1. 파일 파서
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
# 2. 정답지 로더
# ══════════════════════════════════════════════════════

def load_solution() -> str:
    sol_path = Path(CONFIG["solution_file"])
    if not sol_path.exists():
        log.warning(f"정답지 없음 ({sol_path}) — 로직 기반 채점만 합니다.")
        return ""
    if sol_path.suffix.lower() == ".ipynb":
        nb = json.loads(sol_path.read_text(encoding="utf-8"))
        lines = []
        for cell in nb.get("cells", []):
            src = "".join(cell.get("source", []))
            if cell.get("cell_type") == "code":
                lines.append("[코드]\n" + src)
            elif cell.get("cell_type") == "markdown":
                lines.append("[설명]\n" + src)
        return "\n\n".join(lines)
    return sol_path.read_text(encoding="utf-8", errors="replace")


def load_rubric() -> dict:
    rubric_path = Path(CONFIG["rubric_file"])
    if not rubric_path.exists():
        log.error(f"루브릭 없음: {rubric_path}")
        sys.exit(1)
    return json.loads(rubric_path.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════
# 3. 채점 엔진
# ══════════════════════════════════════════════════════

def _rubric_to_prompt(rubric: dict) -> str:
    lines = [f"총점: {rubric.get('total_points', 100)}점"]
    for part in rubric.get("parts", []):
        lines.append(f"\n[{part['name']} — {part['points']}점]")
        for item in part.get("items", []):
            lines.append(f"  {item['id']}. {item['name']} ({item['points']}점) [{item['type']}]")
            lines.append(f"     만점: {item['full_credit']}")
            if item.get("partial_credit"):
                lines.append(f"     부분: {item['partial_credit']}")
            lines.append(f"     0점: {item['no_credit']}")
            if item.get("grading_note"):
                lines.append(f"     ※ {item['grading_note']}")
    return "\n".join(lines)


def grade_submission(file_path: Path, rubric: dict, solution: str) -> dict:
    client = anthropic.Anthropic(api_key=CONFIG["api_key"])
    parsed = parse_file(file_path)

    rubric_text    = _rubric_to_prompt(rubric)
    total_possible = rubric.get("total_points", 100)

    sol_excerpt = solution[:5000] + "\n...(생략)" if len(solution) > 5000 else solution
    sol_section = f"\n\n[정답지 — 로직 비교용]\n{sol_excerpt}" if sol_excerpt else ""

    # 파트 목록을 프롬프트에 명시해서 Claude가 정확히 같은 key 이름으로 반환하게 함
    parts_schema = []
    for part in rubric.get("parts", []):
        item_schema = []
        for item in part.get("items", []):
            item_schema.append(
                f'{{"item_id":"{item["id"]}","name":"{item["name"]}",'
                f'"earned":<점수>,"max":{item["points"]},"verdict":"full/partial/none","reason":"근거"}}'
            )
        parts_schema.append(
            f'{{"part_id":"{part["id"]}","part_name":"{part["name"]}",'
            f'"earned":<점수>,"max":{part["points"]},"items":[{",".join(item_schema)}]}}'
        )

    system = f"""당신은 {rubric.get('course','BUAN 6356')} 과제 채점 교수입니다.

[채점 원칙]
{rubric.get('grading_philosophy','')}
- 로직이 맞으면 코드 스타일 무관하게 만점
- 서술형은 내용 정확성과 논리 위주
- 부분점수 적극 인정

반드시 아래 JSON 형식 그대로, 순수 JSON만 출력하세요 (마크다운 없이):
{{
  "total": <0~{total_possible} 정수>,
  "grade": "<A/B/C/D/F>",
  "part_scores": [
    {chr(10).join(parts_schema)}
  ],
  "code_feedback": "<코딩 피드백 2~3문장>",
  "narrative_feedback": "<서술형 피드백 2~3문장>",
  "overall_feedback": "<종합 피드백 3~4문장>"
}}"""

    user = f"""과제: {rubric.get('assignment','Pinnacle Outdoor Co. Case Study')}
{sol_section}

[루브릭]
{rubric_text}

[학생 파일: {file_path.name}]

── 코드 ──
{parsed['code'][:7000] or '[코드 없음]'}
{'...(생략)' if len(parsed['code']) > 7000 else ''}

── 서술형 답안 ──
{parsed['narrative'][:3000] or '[서술형 없음]'}
{'...(생략)' if len(parsed['narrative']) > 3000 else ''}

JSON으로만 응답하세요."""

    # API 호출 — 실패 시 1회 재시도
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
                log.warning(f"JSON 파싱 실패, 재시도 중... ({e})")
                time.sleep(2)
            else:
                raise RuntimeError(f"JSON 파싱 2회 모두 실패: {e}\n응답 앞부분: {raw[:200]}")

    result["file"]                  = file_path.name
    result["graded_at"]             = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["parsed_code_lines"]     = len(parsed["code"].splitlines())
    result["parsed_narrative_chars"]= len(parsed["narrative"])
    return result


# ══════════════════════════════════════════════════════
# 4. 결과 저장
# ══════════════════════════════════════════════════════

def save_results(result: dict, output_dir: Path, rubric: dict):
    stem = Path(result["file"]).stem
    verdict_label = {"full": "만점", "partial": "부분", "none": "0점"}

    # 피드백 텍스트
    feedback_path = output_dir / f"{stem}_feedback.txt"
    lines = [
        "═" * 50,
        f"  {rubric.get('assignment','과제')} 채점 결과",
        "═" * 50,
        f"  파일    : {result['file']}",
        f"  채점일시: {result['graded_at']}",
        f"  총점    : {result['total']}점 / {rubric.get('total_points',100)}점  ({result.get('grade','-')})",
        f"  (코드 {result.get('parsed_code_lines','?')}줄 / 서술형 {result.get('parsed_narrative_chars','?')}자)",
        "",
    ]
    for part in result.get("part_scores", []):
        lines.append(f"┌─ {part['part_name']}: {part['earned']}/{part['max']}점")
        for item in part.get("items", []):
            v   = verdict_label.get(item.get("verdict", ""), "?")
            sym = "✓" if item["verdict"] == "full" else ("△" if item["verdict"] == "partial" else "✗")
            lines.append(f"│  {sym} {item['name']}: {item['earned']}/{item['max']}점 [{v}]")
            if item.get("reason"):
                lines.append(f"│     → {item['reason']}")
        lines.append("│")

    lines += [
        "", "[ 코딩 피드백 ]",    result.get("code_feedback", ""),
        "", "[ 서술형 피드백 ]",  result.get("narrative_feedback", ""),
        "", "[ 종합 피드백 ]",    result.get("overall_feedback", ""), "",
    ]
    feedback_path.write_text("\n".join(lines), encoding="utf-8")

    # CSV — part_scores의 key는 "part_id" (Claude 응답 기준)
    csv_path    = output_dir / "grading_report.csv"
    write_header = not csv_path.exists()

    rubric_parts     = rubric.get("parts", [])
    rubric_part_ids  = [p["id"] for p in rubric_parts]

    # part_scores에서 part_id로 매핑
    score_map = {p["part_id"]: p["earned"] for p in result.get("part_scores", [])}
    max_map   = {p["part_id"]: p["max"]    for p in result.get("part_scores", [])}

    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if write_header:
            headers = ["파일명", "총점", "만점", "등급", "채점일시"]
            for pid in rubric_part_ids:
                part = next((p for p in rubric_parts if p["id"] == pid), {})
                headers += [f"{part.get('name', pid)}_점수", f"{part.get('name', pid)}_만점"]
            headers.append("종합피드백")
            writer.writerow(headers)

        row = [result["file"], result["total"], rubric.get("total_points", 100),
               result.get("grade", "-"), result["graded_at"]]
        for pid in rubric_part_ids:
            row += [score_map.get(pid, 0), max_map.get(pid, 0)]
        row.append(result.get("overall_feedback", "")[:100] + "…")
        writer.writerow(row)

    log.info(f"  저장 완료 → {feedback_path.name}")


# ══════════════════════════════════════════════════════
# 5. 폴더 감시
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
        log.info(f"새 파일 감지: {path.name}")
        try:
            result = grade_submission(path, self.rubric, self.solution)
            save_results(result, self.output_dir, self.rubric)
            log.info(f"✅ 완료: {path.name} → {result['total']}점 ({result.get('grade','-')})")
        except Exception as e:
            log.error(f"❌ 실패 ({path.name}): {e}")
        finally:
            self.processing.discard(str(path))


def grade_existing_files(watch_dir, output_dir, rubric, solution):
    """시작 시 submissions/ 폴더에 이미 있는 파일을 모두 채점합니다."""
    allowed = CONFIG["allowed_extensions"]
    existing = [
        f for f in watch_dir.iterdir()
        if f.is_file() and f.suffix.lower() in allowed
    ]
    if not existing:
        log.info("  기존 파일 없음 — 새 파일 감시 시작")
        return

    # 이미 채점된 파일은 건너뜀
    to_grade, skipped = [], []
    for f in sorted(existing):
        feedback = output_dir / f"{f.stem}_feedback.txt"
        if feedback.exists():
            skipped.append(f.name)
        else:
            to_grade.append(f)

    if skipped:
        log.info(f"  이미 채점된 파일 {len(skipped)}개 건너뜀: {', '.join(skipped)}")
    if not to_grade:
        log.info("  모든 기존 파일 채점 완료 — 새 파일 감시 시작")
        return

    log.info(f"  기존 파일 {len(to_grade)}개 채점 시작...")
    for i, path in enumerate(to_grade, 1):
        log.info(f"  [{i}/{len(to_grade)}] {path.name}")
        try:
            result = grade_submission(path, rubric, solution)
            save_results(result, output_dir, rubric)
            log.info(f"  ✅ {path.name} → {result['total']}점 ({result.get('grade','-')})")
        except Exception as e:
            log.error(f"  ❌ {path.name} 실패: {e}")


def watch_folder(rubric, solution):
    if not WATCHDOG_OK:
        log.error("watchdog 미설치. pip install watchdog")
        sys.exit(1)

    watch_dir  = Path(CONFIG["watch_folder"])
    output_dir = Path(CONFIG["output_folder"])
    watch_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 55)
    log.info("R/ipynb 과제 자동채점 시스템 시작")
    log.info(f"  과제     : {rubric.get('assignment','?')}")
    log.info(f"  감시 폴더: {watch_dir.resolve()}")
    log.info(f"  결과 폴더: {output_dir.resolve()}")
    log.info(f"  정답지   : {'있음' if solution else '없음'}")
    log.info("=" * 55)

    # 1단계: 기존 파일 일괄 채점 (이미 채점된 건 건너뜀)
    grade_existing_files(watch_dir, output_dir, rubric, solution)

    # 2단계: 새 파일 감시
    log.info("  새 파일 감시 중... (submissions/ 에 파일 추가 시 자동 채점)")
    log.info("  종료: Ctrl+C")

    handler  = SubmissionHandler(output_dir, rubric, solution)
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        log.info("채점 시스템 종료")
    observer.join()


# ══════════════════════════════════════════════════════
# 6. 진입점
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="R/ipynb 과제 자동채점")
    parser.add_argument("--grade-file", metavar="FILE", help="단일 파일 즉시 채점")
    args = parser.parse_args()

    rubric   = load_rubric()
    solution = load_solution()

    if args.grade_file:
        path = Path(args.grade_file)
        log.info(f"단일 채점: {path.name}")
        try:
            result = grade_submission(path, rubric, solution)
            out = Path(CONFIG["output_folder"])
            out.mkdir(parents=True, exist_ok=True)
            save_results(result, out, rubric)
            log.info(f"✅ 완료: {result['total']}점 ({result.get('grade','-')})")
        except Exception as e:
            log.error(f"❌ 채점 실패: {e}")
            sys.exit(1)
    else:
        watch_folder(rubric, solution)