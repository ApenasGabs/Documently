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
    MODEL, MAX_TOKENS, OLLAMA_HOST, EXTRACT_TARGETS,
)
from profiles import detect_profile
from frameworks import detect_framework

# Suporta paths customizados (Windows nativo) ou defaults Docker
PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", "/projects"))
DOCS_DIR     = Path(os.getenv("DOCS_DIR",     "/output/docs"))
STATUS_DIR   = Path(os.getenv("STATUS_DIR",   "/output/status"))
LOGS_DIR     = Path(os.getenv("TELEMETRY_LOG_DIR", "/output/logs"))

DOCS_DIR.mkdir(parents=True, exist_ok=True)
STATUS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


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
    status["extraction_targets"] = EXTRACT_TARGETS
    log_info(f"perfil: {profile['lang_label']} | modelo: {MODEL}", name)
    log_info(f"escopo de extração: {', '.join(EXTRACT_TARGETS) if EXTRACT_TARGETS else 'default'}", name)
    # Detecta framework (determinístico + fallback IA)
    framework = detect_framework(project_path, profile)
    status["framework"] = framework

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
        try:
            result = analyze_file(
                filepath, project_path, context_window,
                status["files"], profile, name, DOCS_DIR,
            )
            if result:
                file_docs.append(result)
        except Exception as e:
            rel = str(filepath)
            status["files"].setdefault(rel, {})
            file_status = status["files"][rel]
            if not file_status.get("started_at"):
                file_status["started_at"] = datetime.now().isoformat()
            status["files"][rel]["done"] = False
            status["files"][rel]["error"] = str(e)
            finished_at = datetime.now()
            status["files"][rel]["finished_at"] = finished_at.isoformat()
            try:
                started_dt = datetime.fromisoformat(file_status["started_at"])
                status["files"][rel]["elapsed_sec"] = round((finished_at - started_dt).total_seconds(), 3)
            except Exception:
                status["files"][rel]["elapsed_sec"] = file_status.get("elapsed_sec", 0.0)
            log_warn(f"falha ao analisar arquivo, seguindo para o próximo: {e}", name)
        save_status(STATUS_DIR, name, status)

    # Resumo geral com árvore de arquivos
    elapsed_min  = (time.time() - start_time) / 60
    summary = generate_summary(name, project_path, profile, file_docs, elapsed_min, framework)
    summary_path = DOCS_DIR / name / "_resumo.md"
    save_summary(summary_path, summary, name)

    status["finished_at"] = datetime.now().isoformat()
    save_status(STATUS_DIR, name, status)
    log_ok(f"concluído em {elapsed_min:.1f} min", name)


def main():
    log_info("Documently iniciando...")
    log_info(f"modelo: {MODEL} | host: {OLLAMA_HOST} | max_tokens: {MAX_TOKENS}")
    log_info(f"escopo de extração ativo: {', '.join(EXTRACT_TARGETS) if EXTRACT_TARGETS else 'default'}")

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
    log_info(f"logs   → {LOGS_DIR}")


if __name__ == "__main__":
    main()