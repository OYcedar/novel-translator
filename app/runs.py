from __future__ import annotations

from pathlib import Path
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.models import book_dir


def runs_dir(root_books_dir: Path, book_id: str) -> Path:
    return book_dir(root_books_dir, book_id) / "runs"


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]


def run_path(root_books_dir: Path, book_id: str, run_id: str) -> Path:
    return runs_dir(root_books_dir, book_id) / f"{run_id}.json"


def load_run(root_books_dir: Path, book_id: str, run_id: str) -> dict:
    path = run_path(root_books_dir, book_id, run_id)
    if not path.exists():
        return {"book": book_id, "run_id": run_id, "created_at": _now(), "batches": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_run(root_books_dir: Path, book_id: str, run: dict) -> Path:
    path = run_path(root_books_dir, book_id, str(run["run_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def record_batch(root_books_dir: Path, book_id: str, run_id: str, batch: dict) -> None:
    run = load_run(root_books_dir, book_id, run_id)
    run.setdefault("batches", []).append(batch)
    run["updated_at"] = _now()
    save_run(root_books_dir, book_id, run)


def run_report(root_books_dir: Path, book_id: str) -> dict:
    runs = _load_all_runs(root_books_dir, book_id)
    batches = [batch for run in runs for batch in run.get("batches", [])]
    failed = [batch for batch in batches if batch.get("status") == "failed"]
    succeeded = [batch for batch in batches if batch.get("status") == "succeeded"]
    return {
        "status": "ok" if not failed else "warning",
        "warnings": [f"存在 {len(failed)} 个失败批次"] if failed else [],
        "summary": {
            "book": book_id,
            "runs": len(runs),
            "batches": len(batches),
            "succeeded": len(succeeded),
            "failed": len(failed),
        },
        "details": {"runs": [{"run_id": run.get("run_id"), "batches": len(run.get("batches", []))} for run in runs[-20:]]},
    }


def failed_batches(root_books_dir: Path, book_id: str) -> dict:
    runs = _load_all_runs(root_books_dir, book_id)
    failed = []
    for run in runs:
        for batch in run.get("batches", []):
            if batch.get("status") == "failed":
                failed.append({**batch, "run_id": run.get("run_id")})
    return {
        "status": "ok" if not failed else "warning",
        "warnings": [],
        "summary": {"book": book_id, "failed": len(failed)},
        "details": {"batches": failed[-100:]},
    }


def latest_failed_paragraph_ids(root_books_dir: Path, book_id: str) -> list[str]:
    report = failed_batches(root_books_dir, book_id)
    ids = []
    seen = set()
    for batch in report["details"]["batches"]:
        for paragraph_id in batch.get("paragraph_ids", []):
            if paragraph_id not in seen:
                seen.add(paragraph_id)
                ids.append(paragraph_id)
    return ids


def _load_all_runs(root_books_dir: Path, book_id: str) -> list[dict]:
    directory = runs_dir(root_books_dir, book_id)
    if not directory.exists():
        return []
    runs = []
    for path in sorted(directory.glob("*.json")):
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return runs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

