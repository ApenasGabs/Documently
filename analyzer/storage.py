"""
Documently — Storage
Leitura e escrita de status e documentação gerada.
"""

import json
from pathlib import Path
from datetime import datetime

from logger import log_ok


def load_status(status_dir: Path, project_name: str) -> dict:
    path = status_dir / f"{project_name}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {
        "project": project_name,
        "profile": None,
        "files": {},
        "started_at": None,
        "finished_at": None,
    }


def save_status(status_dir: Path, project_name: str, status: dict):
    path = status_dir / f"{project_name}.json"
    path.write_text(json.dumps(status, indent=2, ensure_ascii=False))


def save_doc(doc_path: Path, content: str):
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(content, encoding="utf-8")
    log_ok(f"salvo → {doc_path.name}")


def save_summary(summary_path: Path, content: str, project: str):
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(content, encoding="utf-8")
    log_ok(f"resumo salvo → {summary_path.name}", project)