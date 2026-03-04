"""
Documently — Entry point
Orquestra a análise de todos os projetos em /projects.
"""

import os
import time
from datetime import datetime
from pathlib import Path

from logger   import log_info, log_ok, log_warn
from storage  import load_status, save_status, save_summary
from analyzer import (
    wait_for_ollama, check_model_available,
    analyze_file, generate_summary,
    MODEL, MAX_TOKENS, OLLAMA_HOST,
)
from profiles import detect_profile

PROJECTS_DIR = Path("/projects")
DOCS_DIR     = Path("/output/docs")
STATUS_DIR   = Path("/output/status")

DOCS_DIR.mkdir(parents=True, exist_ok=True)
STATUS_DIR.mkdir(parents=True, exist_ok=True)


def process_project(project_path: Path):
    name = project_path.name
    log_info("=" * 50, name)
    log_info("iniciando projeto", name)

    status = load_status(STATUS_DIR, name)
    if status.get("finished_at"):
        log_warn(f"já finalizado em {status['finished_at']}", name)
        return

    profile = detect_profile(project_path)
    status["profile"] = profile["lang_label"]
    log_info(f"perfil: {profile['lang_label']} | modelo: {MODEL}", name)

    if not status["started_at"]:
        status["started_at"] = datetime.now().isoformat()

    files = sorted([
        f for ext in profile["extensions"]
        for f in project_path.rglob(f"*{ext}")
        if not any(part in profile["ignore_dirs"] for part in f.parts)
    ])

    if not files:
        log_warn(f"nenhum arquivo {profile['extensions']} encontrado", name)
        status["finished_at"] = datetime.now().isoformat()
        save_status(STATUS_DIR, name, status)
        return

    log_info(f"{len(files)} arquivo(s) encontrado(s)", name)

    context_window: list[str] = []
    file_docs:      list[dict] = []
    start_time = time.time()

    for i, filepath in enumerate(files):
        log_info(f"[{i+1}/{len(files)}] {filepath.relative_to(project_path)}", name)
        result = analyze_file(
            filepath, project_path, context_window,
            status["files"], profile, name, DOCS_DIR,
        )
        if result:
            file_docs.append(result)
        save_status(STATUS_DIR, name, status)

    # Resumo geral com árvore de arquivos
    elapsed_min  = (time.time() - start_time) / 60
    summary      = generate_summary(name, project_path, profile, file_docs, elapsed_min)
    summary_path = DOCS_DIR / name / "_resumo.md"
    save_summary(summary_path, summary, name)

    status["finished_at"] = datetime.now().isoformat()
    save_status(STATUS_DIR, name, status)
    log_ok(f"concluído em {elapsed_min:.1f} min", name)


def main():
    log_info("Documently iniciando...")
    log_info(f"modelo: {MODEL} | host: {OLLAMA_HOST} | max_tokens: {MAX_TOKENS}")

    wait_for_ollama()
    check_model_available()

    entries = list(PROJECTS_DIR.iterdir()) if PROJECTS_DIR.exists() else []
    log_info(f"projetos encontrados ({len(entries)}): {[e.name for e in entries if e.is_dir()]}")

    projects = sorted([p for p in PROJECTS_DIR.iterdir() if p.is_dir()])
    if not projects:
        log_warn("nenhum projeto encontrado em /projects")
        return

    for project in projects:
        process_project(project)

    log_ok("todos os projetos concluídos!")
    log_info("docs   → /output/docs/")
    log_info("status → /output/status/")


if __name__ == "__main__":
    main()