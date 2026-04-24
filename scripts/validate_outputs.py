"""Mechanical validator for the writing stage's .docx artefacts.

Walks ``data/outputs/{run_id_short}/`` (or the newest run dir if the argument is
omitted), pairs every ``*_resume.docx`` with its ``*_cover.docx``, and runs a
small battery of sanity checks: minimum file size, candidate name present,
company slug present, resume has at least three bullets, neither document uses
banned cliches. Exits 0 when at least three pairs pass every check, 2 otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document

OUTPUTS_ROOT = Path("data/outputs")
CANDIDATE_NAME = "Davi Almeida"
MIN_SIZE_BYTES = 5000
MIN_BULLETS = 3
BANNED_CLICHES = [
    "synergy",
    "synergies",
    "spearheaded",
    "rockstar",
    "ninja",
    "guru",
    "wizard",
    "10x engineer",
    "proven track record",
    "world-class",
    "cutting-edge",
    "think outside the box",
    "move the needle",
]


def extract_text_and_bullets(docx_path: Path) -> tuple[str, int]:
    """Return ``(full_text, bullet_count)``.

    A paragraph counts as a bullet when its style name contains "Bullet" or
    "List", or when the raw text starts with a bullet glyph.
    """
    doc = Document(str(docx_path))
    lines: list[str] = []
    bullets = 0
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        lines.append(text)
        style_name = getattr(getattr(para, "style", None), "name", "") or ""
        if "Bullet" in style_name or "List" in style_name:
            bullets += 1
            continue
        if text.startswith(("- ", "• ", "* ", "\u2022 ")):
            bullets += 1
    return "\n".join(lines), bullets


def _find_cliches(text: str) -> list[str]:
    lower = text.lower()
    return [c for c in BANNED_CLICHES if c in lower]


def validate_pair(
    resume_path: Path, cover_path: Path, company_hint: str
) -> list[tuple[str, str, bool, str]]:
    """Return a list of ``(role, check_name, passed, detail)`` tuples."""
    results: list[tuple[str, str, bool, str]] = []
    for path, role in [(resume_path, "resume"), (cover_path, "cover")]:
        if not path.exists():
            results.append((role, "exists", False, f"missing: {path.name}"))
            continue
        results.append((role, "exists", True, path.name))

        size = path.stat().st_size
        results.append(
            (role, f"size>={MIN_SIZE_BYTES}B", size >= MIN_SIZE_BYTES, f"{size}B")
        )

        try:
            text, bullets = extract_text_and_bullets(path)
        except Exception as e:  # noqa: BLE001
            results.append((role, "parse", False, str(e)[:120]))
            continue
        results.append((role, "parse", True, f"{len(text)}ch"))

        results.append((role, "has_name", CANDIDATE_NAME in text, CANDIDATE_NAME))

        if company_hint and role == "cover":
            hint_norm = company_hint.replace("_", " ").lower()
            first_word = hint_norm.split()[0] if hint_norm.split() else ""
            body_norm = text.lower().replace("-", " ").replace("_", " ")
            ok = bool(first_word) and first_word in body_norm
            results.append((role, "has_company", ok, f"hint={first_word!r}"))

        if role == "resume":
            results.append(
                (role, f"bullets>={MIN_BULLETS}", bullets >= MIN_BULLETS, f"{bullets}")
            )

        hits = _find_cliches(text)
        results.append((role, "no_cliches", not hits, ",".join(hits) or "clean"))
    return results


def _pair_ok(checks: list[tuple[str, str, bool, str]]) -> bool:
    return all(passed for _, _, passed, _ in checks)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    run_id_short = sys.argv[1] if len(sys.argv) > 1 else None

    if run_id_short:
        run_dir = OUTPUTS_ROOT / run_id_short
    else:
        if not OUTPUTS_ROOT.exists():
            print(f"outputs root missing: {OUTPUTS_ROOT}", file=sys.stderr)
            return 1
        candidates = [p for p in OUTPUTS_ROOT.iterdir() if p.is_dir()]
        if not candidates:
            print(f"no run directories under {OUTPUTS_ROOT}", file=sys.stderr)
            return 1
        run_dir = max(candidates, key=lambda p: p.stat().st_mtime)

    if not run_dir.exists():
        print(f"run dir not found: {run_dir}", file=sys.stderr)
        return 1

    resumes = sorted(run_dir.glob("*_resume.docx"))
    if not resumes:
        print(f"no *_resume.docx in {run_dir}")
        return 2

    total, passed_pairs = 0, 0
    print(f"Validating {run_dir} ({len(resumes)} resume(s))\n")
    for resume in resumes:
        base = resume.name[: -len("_resume.docx")]
        cover = resume.parent / f"{base}_cover.docx"
        hint = base.split("_")[0] if "_" in base else base
        checks = validate_pair(resume, cover, hint)
        total += 1
        ok = _pair_ok(checks)
        passed_pairs += int(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {base}")
        for role, name, passed, detail in checks:
            mark = "✓" if passed else "✗"
            print(f"    {mark} {role}.{name}: {detail}")
        print()

    print(f"Summary: {passed_pairs}/{total} pair(s) passed all checks")
    return 0 if passed_pairs >= 3 else 2


if __name__ == "__main__":
    sys.exit(main())
