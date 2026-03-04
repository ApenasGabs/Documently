"""
Documently — Analyzer
Comunicação com Ollama: chunks, análise de arquivos e geração de resumo.
"""

import os
import time
import requests
from pathlib import Path
from datetime import datetime

from logger import log_info, log_ok, log_warn, log_err, log_skip

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL       = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
MAX_TOKENS  = int(os.getenv("MAX_TOKENS_PER_CHUNK", 3000))


# ── Ollama ────────────────────────────────────────────────────────────

def wait_for_ollama(retries: int = 20, delay: int = 3):
    log_info("aguardando Ollama ficar pronto...")
    for i in range(retries):
        try:
            r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
            if r.status_code == 200:
                log_ok(f"Ollama pronto em {OLLAMA_HOST}")
                return
        except Exception as e:
            log_warn(f"tentativa {i+1}/{retries} falhou: {e}")
        time.sleep(delay)
    raise RuntimeError("Ollama não respondeu a tempo.")


def check_model_available():
    """Verifica se o modelo está disponível antes de começar."""
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        if not any(MODEL in m for m in models):
            raise RuntimeError(
                f"Modelo '{MODEL}' não encontrado no Ollama. "
                f"Disponíveis: {models}. "
                f"Baixe com: docker exec documently-ollama-1 ollama pull {MODEL}"
            )
        log_ok(f"modelo '{MODEL}' disponível")
    except requests.RequestException as e:
        raise RuntimeError(f"Não foi possível verificar modelos: {e}")


def call_ollama(prompt: str) -> str:
    response = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": 4096,
                "temperature": 0.1,
                "num_predict": 512,
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["response"].strip()


# ── Chunks ────────────────────────────────────────────────────────────

def chunk_text(content: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    """Divide o conteúdo em chunks respeitando o limite de tokens estimado."""
    lines = content.splitlines(keepends=True)
    chunks, current, count = [], [], 0
    for line in lines:
        t = len(line) // 4  # ~4 chars por token
        if count + t > max_tokens and current:
            chunks.append("".join(current))
            current, count = [line], t
        else:
            current.append(line)
            count += t
    if current:
        chunks.append("".join(current))
    return chunks or [""]


# ── Árvore de arquivos ────────────────────────────────────────────────

def build_tree(project_path: Path, profile: dict) -> str:
    """Gera árvore de arquivos relevantes do projeto."""
    files = sorted([
        f.relative_to(project_path)
        for ext in profile["extensions"]
        for f in project_path.rglob(f"*{ext}")
        if not any(part in profile["ignore_dirs"] for part in f.parts)
    ])

    # Monta árvore agrupando por pasta
    tree = [f"{project_path.name}/"]
    seen_dirs: set = set()
    for f in files:
        # Adiciona pastas intermediárias
        for i in range(len(f.parts) - 1):
            dir_path = Path(*f.parts[:i+1])
            if dir_path not in seen_dirs:
                depth = i
                tree.append(f"{'  ' * depth}├── {f.parts[i]}/")
                seen_dirs.add(dir_path)
        # Adiciona o arquivo
        depth = len(f.parts) - 1
        tree.append(f"{'  ' * depth}└── {f.name}")

    return "\n".join(tree)


# ── Análise ───────────────────────────────────────────────────────────

def analyze_chunk(chunk: str, context_summary: str, filename: str,
                  chunk_idx: int, total: int, profile: dict) -> str:
    ext = Path(filename).suffix
    prompt = f"""Você é um analisador de código especialista em {profile['lang_label']}.

Contexto já analisado (resumo): {context_summary[-600:] if context_summary else "Nenhum — este é o início do arquivo."}

Arquivo: {filename}  (chunk {chunk_idx + 1} de {total})

```{ext.lstrip('.')}
{chunk}
```

{profile['prompt_focus']}

Responda em português, de forma concisa (máx 300 palavras)."""
    return call_ollama(prompt)


def analyze_file(filepath: Path, project_path: Path, context_window: list,
                 status_files: dict, profile: dict, project: str,
                 docs_dir: Path) -> dict | None:
    """Analisa um arquivo inteiro chunk a chunk. Salva doc individual."""
    rel         = str(filepath)
    fname       = filepath.name
    file_status = status_files.get(rel, {"chunks_done": 0, "total_chunks": 0, "done": False})

    try:
        content = filepath.read_text(errors="replace")
    except Exception as e:
        log_err(f"não foi possível ler: {e}", project, fname)
        return None

    chunks = chunk_text(content)
    total  = len(chunks)
    file_status["total_chunks"] = total
    status_files[rel] = file_status

    if file_status.get("done"):
        log_skip("já analisado anteriormente", project, fname)
        return None

    log_info(f"iniciando — {total} chunk(s)", project, fname)
    context_summary = "\n".join(context_window[-5:])
    doc_parts       = [f"# 📄 `{fname}`\n\n_Perfil: {profile['lang_label']}_\n"]
    full_analysis   = []

    for i, chunk in enumerate(chunks):
        if i < file_status["chunks_done"]:
            log_skip(f"chunk {i+1}/{total} já processado", project, fname)
            continue

        log_info(f"chunk {i+1}/{total} → enviando para Ollama...", project, fname)
        start    = time.time()
        analysis = analyze_chunk(chunk, context_summary, fname, i, total, profile)
        elapsed  = round(time.time() - start, 1)
        log_ok(f"chunk {i+1}/{total} concluído em {elapsed}s", project, fname)

        doc_parts.append(f"## Chunk {i+1}/{total}\n\n{analysis}\n")
        full_analysis.append(analysis)

        context_window.append(f"[{fname} c{i+1}]: {analysis[:200]}")
        if len(context_window) > 20:
            context_window.pop(0)

        file_status["chunks_done"] = i + 1
        status_files[rel] = file_status

    # Salva doc individual espelhando estrutura de pastas
    relative_path = filepath.relative_to(project_path)
    doc_path      = docs_dir / project / relative_path.with_suffix(".md")
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text("\n".join(doc_parts), encoding="utf-8")

    file_status["done"]     = True
    file_status["doc_path"] = str(doc_path)
    status_files[rel]       = file_status

    log_ok(f"salvo → {doc_path.relative_to(docs_dir)}", project, fname)
    return {"path": str(relative_path), "analysis": " ".join(full_analysis)}


def generate_summary(project_name: str, project_path: Path, profile: dict,
                     file_docs: list[dict], elapsed_min: float) -> str:
    """Gera o _resumo.md consolidando toda a análise do projeto."""
    log_info("gerando resumo geral...", project_name)

    tree    = build_tree(project_path, profile)
    context = "\n".join([f"- {d['path']}: {d['analysis'][:300]}" for d in file_docs])

    prompt = f"""Você é um arquiteto de software sênior. Analise os dados abaixo sobre o projeto "{project_name}" e gere um resumo executivo completo.

Perfil detectado: {profile['lang_label']}
Total de arquivos analisados: {len(file_docs)}
Tempo de análise: {elapsed_min:.1f} minutos

Estrutura do projeto:
```
{tree}
```

Resumo por arquivo:
{context[:5000]}

Gere um relatório em português com as seguintes seções:

## Visão Geral
[Descreva o propósito do projeto em 2-3 frases]

## Arquitetura
[Como o projeto está organizado, padrões identificados]

## Arquivos por Responsabilidade
[Agrupe os arquivos por função: API, componentes, hooks, utils, etc]

## Dependências Principais
[Liste as bibliotecas externas identificadas]

## Pontos de Atenção
[Riscos, más práticas ou pontos que merecem revisão]

Seja objetivo e técnico."""

    summary = call_ollama(prompt)
    header  = (
        f"# 📋 Resumo: `{project_name}`\n\n"
        f"_Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} · "
        f"Perfil: **{profile['lang_label']}** · "
        f"{len(file_docs)} arquivo(s) · "
        f"{elapsed_min:.1f} min_\n\n"
        f"## 🗂 Estrutura\n\n```\n{tree}\n```\n\n---\n\n"
    )
    return header + summary