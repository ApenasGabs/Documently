#!/usr/bin/env python3
"""
Documently — Monitor de progresso

Lê os projetos, detecta perfil, conta arquivos elegíveis e cruza com os
arquivos já concluídos no status para exibir progresso real.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path

from analyzer.profiles import detect_profile


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}m"
    if minutes > 0:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def project_elapsed_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    started = parse_iso_datetime(started_at)
    if not started:
        return None
    finished = parse_iso_datetime(finished_at)
    end = finished or datetime.now()
    return max(0.0, (end - started).total_seconds())


def resolve_projects_dir() -> Path:
    env_path = os.getenv("PROJECTS_DIR")
    if env_path:
        return Path(env_path)

    docker_default = Path("/projects")
    if docker_default.exists() and any(docker_default.iterdir()):
        return docker_default

    return Path(__file__).parent / "projects"


def resolve_status_dir() -> Path:
    env_path = os.getenv("STATUS_DIR")
    if env_path:
        return Path(env_path)

    docker_default = Path("/output/status")
    if docker_default.exists():
        return docker_default

    return Path(__file__).parent / "status"


def resolve_docs_dir() -> Path:
    env_path = os.getenv("DOCS_DIR")
    if env_path:
        return Path(env_path)

    docker_default = Path("/output/docs")
    if docker_default.exists():
        return docker_default

    return Path(__file__).parent / "docs"


def silent_detect_profile(project_path: Path) -> dict:
    sink = StringIO()
    with redirect_stdout(sink):
        return detect_profile(project_path)


def list_candidate_files(project_path: Path, profile: dict) -> list[Path]:
    files = sorted([
        f for ext in profile["extensions"]
        for f in project_path.rglob(f"*{ext}")
        if not any(part in profile["ignore_dirs"] for part in f.parts)
    ])
    return files


def load_status(status_dir: Path, project_name: str) -> dict:
    path = status_dir / f"{project_name}.json"
    if not path.exists():
        return {
            "project": project_name,
            "profile": None,
            "files": {},
            "started_at": None,
            "finished_at": None,
        }

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "project": project_name,
            "profile": None,
            "files": {},
            "started_at": None,
            "finished_at": None,
            "_status_read_error": True,
        }


def to_project_relative(path_str: str, project_name: str) -> str | None:
    normalized = path_str.replace("\\", "/")
    marker = f"/{project_name}/"

    if marker in normalized:
        return normalized.split(marker, 1)[1]
    if normalized.startswith(project_name + "/"):
        return normalized[len(project_name) + 1:]

    return None


def doc_path_to_local(doc_path: str | None, project_name: str, docs_dir: Path) -> Path | None:
    if not doc_path:
        return None

    rel = to_project_relative(doc_path, project_name)
    if not rel:
        return None

    return docs_dir / project_name / rel


def estimate_recent_seconds_per_file(status_by_rel: dict[str, dict],
                                     candidate_rel_paths: set[str],
                                     project_name: str,
                                     docs_dir: Path,
                                     window: int = 6) -> float | None:
    timestamps: list[float] = []

    for rel in candidate_rel_paths:
        file_data = status_by_rel.get(rel, {})
        if file_data.get("done") is not True:
            continue

        local_doc = doc_path_to_local(file_data.get("doc_path"), project_name, docs_dir)
        if local_doc and local_doc.exists():
            try:
                timestamps.append(local_doc.stat().st_mtime)
            except OSError:
                continue

    if len(timestamps) < 2:
        return None

    timestamps = sorted(timestamps)[-window:]
    if len(timestamps) < 2:
        return None

    span = max(1.0, timestamps[-1] - timestamps[0])
    return span / max(1, len(timestamps) - 1)


def format_eta(started_at: str | None,
               done: int,
               total: int,
               finished_at: str | None,
               recent_seconds_per_file: float | None = None,
               paused: bool = False) -> str:
    if finished_at:
        return "concluído"
    if paused and done < total:
        return "pausado"
    if not started_at or done <= 0 or total <= 0 or done >= total:
        if recent_seconds_per_file and total > done:
            remaining = int(recent_seconds_per_file * (total - done))
        else:
            return "-"
    else:
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            if recent_seconds_per_file and total > done:
                remaining = int(recent_seconds_per_file * (total - done))
            else:
                return "-"
        else:
            elapsed_seconds = max(1, (datetime.now() - started).total_seconds())
            avg_per_file = elapsed_seconds / done
            base_remaining = int(avg_per_file * (total - done))

            if recent_seconds_per_file:
                recent_remaining = int(recent_seconds_per_file * (total - done))
                remaining = min(base_remaining, recent_remaining)
            else:
                remaining = base_remaining

    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"~{hours}h{minutes:02d}"
    if minutes > 0:
        return f"~{minutes}m{seconds:02d}s"
    return f"~{seconds}s"


def build_snapshot(projects_dir: Path, status_dir: Path, docs_dir: Path,
                   project_filter: str | None) -> tuple[list[dict], dict]:
    if not projects_dir.exists():
        return [], {
            "projects_total": 0,
            "files_total": 0,
            "files_done": 0,
            "files_pending": 0,
            "files_error": 0,
            "progress_pct": 0.0,
        }

    projects = sorted([p for p in projects_dir.iterdir() if p.is_dir()])
    if project_filter:
        projects = [p for p in projects if p.name == project_filter]

    rows: list[dict] = []

    for project_path in projects:
        profile = silent_detect_profile(project_path)
        candidates = list_candidate_files(project_path, profile)
        status_path = status_dir / f"{project_path.name}.json"
        status = load_status(status_dir, project_path.name)

        candidate_rel_paths = {
            str(p.relative_to(project_path)).replace("\\", "/")
            for p in candidates
        }
        status_files = status.get("files", {})

        status_by_rel: dict[str, dict] = {}
        for raw_path, file_data in status_files.items():
            rel = to_project_relative(raw_path, project_path.name)
            if rel:
                status_by_rel[rel] = file_data

        recent_seconds = estimate_recent_seconds_per_file(
            status_by_rel,
            candidate_rel_paths,
            project_path.name,
            docs_dir,
        )
        paused = False
        if status_path.exists() and not status.get("finished_at"):
            try:
                seconds_since_update = time.time() - status_path.stat().st_mtime
                paused = seconds_since_update > 15 * 60
            except OSError:
                paused = False

        done = sum(1 for rel in candidate_rel_paths if status_by_rel.get(rel, {}).get("done") is True)
        errors = sum(
            1
            for rel in candidate_rel_paths
            if status_by_rel.get(rel, {}).get("error")
            and status_by_rel.get(rel, {}).get("done") is not True
        )
        total = len(candidates)
        pending = max(0, total - done)
        progress = (done / total * 100.0) if total else 0.0

        rows.append({
            "project": project_path.name,
            "profile": profile.get("lang_label", "-"),
            "extensions": ",".join(profile.get("extensions", [])),
            "total": total,
            "done": done,
            "pending": pending,
            "errors": errors,
            "progress": progress,
            "started_at": status.get("started_at"),
            "finished_at": status.get("finished_at"),
            "elapsed_sec": project_elapsed_seconds(status.get("started_at"), status.get("finished_at")),
            "eta": format_eta(
                status.get("started_at"),
                done,
                total,
                status.get("finished_at"),
                recent_seconds_per_file=recent_seconds,
                paused=paused,
            ),
        })

    files_total = sum(r["total"] for r in rows)
    files_done = sum(r["done"] for r in rows)
    files_pending = sum(r["pending"] for r in rows)
    files_error = sum(r["errors"] for r in rows)
    progress_pct = (files_done / files_total * 100.0) if files_total else 0.0
    elapsed_total_sec = sum((r.get("elapsed_sec") or 0.0) for r in rows)

    summary = {
        "projects_total": len(rows),
        "files_total": files_total,
        "files_done": files_done,
        "files_pending": files_pending,
        "files_error": files_error,
        "progress_pct": progress_pct,
        "elapsed_total_sec": elapsed_total_sec,
    }
    return rows, summary


def print_snapshot(rows: list[dict], summary: dict):
    print("\n=== Documently Progress Monitor ===")
    print(
        f"Projetos: {summary['projects_total']} | "
        f"Arquivos: {summary['files_done']}/{summary['files_total']} "
        f"({summary['progress_pct']:.1f}%) | "
        f"Pendentes: {summary['files_pending']} | "
        f"Erros: {summary['files_error']} | "
        f"Tempo total: {format_duration(summary.get('elapsed_total_sec'))}"
    )

    if not rows:
        print("Nenhum projeto encontrado para monitorar.")
        return

    header = (
        f"{'Projeto':28} {'Perfil':20} {'Progresso':10} {'Done/Total':11} "
        f"{'Pend':5} {'Err':4} {'Tempo':10} {'ETA':10}"
    )
    print("\n" + header)
    print("-" * len(header))

    for row in rows:
        print(
            f"{row['project'][:28]:28} "
            f"{row['profile'][:20]:20} "
            f"{row['progress']:6.1f}%   "
            f"{row['done']:4}/{row['total']:<6} "
            f"{row['pending']:<5} "
            f"{row['errors']:<4} "
            f"{format_duration(row.get('elapsed_sec'))[:10]:10} "
            f"{row['eta'][:10]:10}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitora progresso da análise (sem IA)")
    parser.add_argument("--projects-dir", type=Path, default=None, help="Diretório de projetos")
    parser.add_argument("--status-dir", type=Path, default=None, help="Diretório de status")
    parser.add_argument("--project", type=str, default=None, help="Filtra por nome de projeto")
    parser.add_argument("--watch", type=int, default=0, help="Atualiza a cada N segundos")
    parser.add_argument("--json", action="store_true", help="Saída em JSON")
    return parser.parse_args()


def main():
    args = parse_args()
    projects_dir = args.projects_dir or resolve_projects_dir()
    status_dir = args.status_dir or resolve_status_dir()
    docs_dir = resolve_docs_dir()

    def run_once():
        rows, summary = build_snapshot(projects_dir, status_dir, docs_dir, args.project)
        if args.json:
            print(json.dumps({"summary": summary, "projects": rows}, ensure_ascii=False, indent=2))
        else:
            print_snapshot(rows, summary)

    if args.watch and args.watch > 0:
        try:
            while True:
                os.system("clear")
                print(f"Atualização: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
                run_once()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        run_once()


if __name__ == "__main__":
    main()
