"""
Documently — Analyzer
Fluxo semântico de 3 passos por arquivo:
  1. Scan  → lista assinaturas de funções/classes
  2. Deep  → analisa cada função individualmente
  3. Synth → sintetiza as anotações em doc do arquivo
"""

import os
import time
import requests
from pathlib import Path
from datetime import datetime

from logger    import log_info, log_ok, log_warn, log_err, log_skip
from extractor import extract_functions, functions_to_scan_prompt, FunctionNode

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
    try:
        r      = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        if not any(MODEL in m for m in models):
            raise RuntimeError(
                f"Modelo '{MODEL}' não encontrado. "
                f"Disponíveis: {models}. "
                f"Baixe com: docker exec documently-ollama-1 ollama pull {MODEL}"
            )
        log_ok(f"modelo '{MODEL}' disponível")
    except requests.RequestException as e:
        raise RuntimeError(f"Não foi possível verificar modelos: {e}")


def call_ollama(prompt: str, num_predict: int = 1024) -> str:
    """
    Chama Ollama e detecta truncamento via done_reason == 'length'.
    Se truncar, retenta com prompt reduzido e num_predict dobrado.
    """
    for attempt in range(2):
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model":  MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_ctx":     4096,
                    "temperature": 0.1,
                    "num_predict": num_predict,
                },
            },
            timeout=180,
        )
        response.raise_for_status()
        data      = response.json()
        text      = data.get("response", "").strip()
        truncated = data.get("done_reason") == "length"

        if truncated and attempt == 0:
            log_warn(f"resposta truncada — retentando com {num_predict * 2} tokens")
            num_predict = min(num_predict * 2, 2048)
            prompt      = prompt[: len(prompt) // 2] + "\n\n[prompt reduzido]\n\nContinue a documentação:"
            continue

        if truncated:
            text += "\n\n> ⚠️ _Análise truncada — use um modelo maior ou reduza MAX_TOKENS_PER_CHUNK._"

        return text

    return text


# ── chunk_text (mantido para resumo e fallback) ───────────────────────

def chunk_text(content: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    lines = content.splitlines(keepends=True)
    chunks, current, count = [], [], 0
    for line in lines:
        t = len(line) // 4
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
    files = sorted([
        f.relative_to(project_path)
        for ext in profile["extensions"]
        for f in project_path.rglob(f"*{ext}")
        if not any(part in profile["ignore_dirs"] for part in f.parts)
    ])
    tree      = [f"{project_path.name}/"]
    seen_dirs: set = set()
    for f in files:
        for i in range(len(f.parts) - 1):
            dir_path = Path(*f.parts[: i + 1])
            if dir_path not in seen_dirs:
                tree.append(f"{'  ' * i}├── {f.parts[i]}/")
                seen_dirs.add(dir_path)
        depth = len(f.parts) - 1
        tree.append(f"{'  ' * depth}└── {f.name}")
    return "\n".join(tree)


# ── Fluxo semântico de 3 passos ───────────────────────────────────────

def _step_scan(nodes: list[FunctionNode], filename: str, lang_label: str) -> str:
    """
    Passo 1 — Scan rápido: lista assinaturas e pede ao modelo
    uma linha de descrição para cada função.
    """
    scan_list = functions_to_scan_prompt(nodes, filename)
    prompt = (
        f"Você é um especialista em {lang_label}.\n\n"
        f"{scan_list}\n\n"
        f"Para cada item acima, escreva UMA linha descrevendo o que ela faz. "
        f"Formato: `nome` — descrição. Seja conciso."
    )
    return call_ollama(prompt, num_predict=512)


def _step_deep(node: FunctionNode, context: str, lang_label: str) -> str:
    """
    Passo 2 — Deep dive: analisa o corpo de uma função individualmente.
    """
    num_predict = max(256, min(len(node.body) // 4, 1024))
    prompt = (
        f"Você é um especialista em {lang_label}.\n\n"
        f"Contexto do arquivo até agora:\n{context[-400:]}\n\n"
        f"Analise apenas esta {'classe' if node.kind == 'class' else 'função'}:\n\n"
        f"```\n{node.body[:3000]}\n```\n\n"
        f"Documente de forma concisa (máx 150 palavras):\n"
        f"- O que faz\n"
        f"- Parâmetros e retorno (se houver)\n"
        f"- Efeitos colaterais ou chamadas externas relevantes"
    )
    return call_ollama(prompt, num_predict=num_predict)


def _step_synth(annotations: list[dict], filename: str,
                lang_label: str, profile: dict) -> str:
    """
    Passo 3 — Síntese: gera o doc completo do arquivo a partir das anotações.
    """
    annot_text = "\n\n".join([
        f"### `{a['name']}` ({a['kind']})\n{a['doc']}"
        for a in annotations
    ])
    prompt = (
        f"Você é um especialista em {lang_label}.\n\n"
        f"Abaixo estão as anotações individuais de cada função/classe "
        f"do arquivo `{filename}`:\n\n"
        f"{annot_text[:5000]}\n\n"
        f"{profile['prompt_focus']}\n\n"
        f"Com base nas anotações acima, gere a documentação completa do arquivo. "
        f"Inclua: propósito geral, funções exportadas, dependências externas, "
        f"efeitos colaterais e pontos de atenção. Máx 400 palavras."
    )
    return call_ollama(prompt, num_predict=1024)


def analyze_file(filepath: Path, project_path: Path, context_window: list,
                 status_files: dict, profile: dict, project: str,
                 docs_dir: Path) -> dict | None:
    """
    Analisa um arquivo usando o fluxo semântico de 3 passos.
    Fallback para chunk_text se tree-sitter não encontrar nenhuma função.
    """
    rel   = str(filepath)
    fname = filepath.name
    file_status = status_files.get(rel, {"done": False, "steps": {}})

    try:
        content = filepath.read_text(errors="replace")
    except Exception as e:
        log_err(f"não foi possível ler: {e}", project, fname)
        return None

    if file_status.get("done"):
        log_skip("já analisado anteriormente", project, fname)
        return None

    # Detecta lang_key a partir do perfil
    lang_key   = profile.get("lang_key", "javascript")
    lang_label = profile["lang_label"]

    # ── Extrai funções ────────────────────────────────────────────────
    nodes = extract_functions(content, lang_key, fname)

    doc_parts    = [f"# 📄 `{fname}`\n\n_Perfil: {lang_label}_\n"]
    full_analysis = []
    context_summary = "\n".join(context_window[-5:])

    if not nodes:
        # Fallback: arquivo sem funções detectadas (config, types, etc.)
        log_info(f"sem funções detectadas — análise direta", project, fname)
        chunks = chunk_text(content)
        for i, chunk in enumerate(chunks):
            log_info(f"chunk {i+1}/{len(chunks)} → Ollama...", project, fname)
            num_predict = max(512, min(len(chunk) // 4 // 2, 2048))
            prompt = (
                f"Você é um especialista em {lang_label}.\n\n"
                f"Contexto: {context_summary[-400:]}\n\n"
                f"Arquivo: {fname}\n\n```\n{chunk}\n```\n\n"
                f"{profile['prompt_focus']}\n\nMáx 300 palavras."
            )
            analysis = call_ollama(prompt, num_predict=num_predict)
            doc_parts.append(f"## Análise\n\n{analysis}\n")
            full_analysis.append(analysis)
    else:
        log_info(f"{len(nodes)} função(ões)/classe(s) encontrada(s)", project, fname)

        # ── Passo 1: Scan ─────────────────────────────────────────────
        log_info("passo 1/3 — scan de assinaturas", project, fname)
        scan_result = _step_scan(nodes, fname, lang_label)
        doc_parts.append(f"## Visão Geral das Funções\n\n{scan_result}\n")

        # ── Passo 2: Deep dive por função ─────────────────────────────
        log_info("passo 2/3 — análise individual", project, fname)
        annotations = []
        running_ctx = context_summary

        for node in nodes:
            log_info(f"  analisando `{node.name}` (L{node.start_line}–{node.end_line})", project, fname)
            doc = _step_deep(node, running_ctx, lang_label)
            annotations.append({"name": node.name, "kind": node.kind, "doc": doc})
            running_ctx += f"\n[{node.name}]: {doc[:150]}"

        annot_section = "\n\n".join([
            f"### `{a['name']}` ({a['kind']})\n{a['doc']}"
            for a in annotations
        ])
        doc_parts.append(f"## Documentação por Função\n\n{annot_section}\n")

        # ── Passo 3: Síntese ──────────────────────────────────────────
        log_info("passo 3/3 — síntese do arquivo", project, fname)
        synth = _step_synth(annotations, fname, lang_label, profile)
        doc_parts.append(f"## Resumo do Arquivo\n\n{synth}\n")
        full_analysis = [a["doc"] for a in annotations] + [synth]

        # Atualiza janela de contexto com síntese
        context_window.append(f"[{fname}]: {synth[:200]}")
        if len(context_window) > 20:
            context_window.pop(0)

    # ── Salva doc espelhando estrutura de pastas ──────────────────────
    relative_path = filepath.relative_to(project_path)
    doc_path      = docs_dir / project / relative_path.with_suffix(".md")
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text("\n".join(doc_parts), encoding="utf-8")

    file_status["done"]     = True
    file_status["doc_path"] = str(doc_path)
    status_files[rel]       = file_status

    log_ok(f"salvo → {doc_path.relative_to(docs_dir)}", project, fname)
    return {"path": str(relative_path), "analysis": " ".join(full_analysis)}


# ── Resumo do projeto ─────────────────────────────────────────────────

def generate_summary(project_name: str, project_path: Path, profile: dict,
                     file_docs: list[dict], elapsed_min: float) -> str:
    log_info("gerando resumo geral...", project_name)

    tree    = build_tree(project_path, profile)
    context = "\n".join([f"- {d['path']}: {d['analysis'][:300]}" for d in file_docs])

    prompt = (
        f"Você é um arquiteto de software sênior.\n\n"
        f"Projeto: {project_name}\n"
        f"Perfil: {profile['lang_label']}\n"
        f"Arquivos analisados: {len(file_docs)}\n"
        f"Tempo: {elapsed_min:.1f} min\n\n"
        f"Estrutura:\n```\n{tree}\n```\n\n"
        f"Resumo por arquivo:\n{context[:5000]}\n\n"
        f"Gere um relatório técnico em português com:\n"
        f"## Visão Geral\n"
        f"## Arquitetura\n"
        f"## Arquivos por Responsabilidade\n"
        f"## Dependências Principais\n"
        f"## Pontos de Atenção\n\n"
        f"Seja objetivo e técnico."
    )

    summary = call_ollama(prompt, num_predict=1500)
    header  = (
        f"# 📋 Resumo: `{project_name}`\n\n"
        f"_Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} · "
        f"Perfil: **{profile['lang_label']}** · "
        f"{len(file_docs)} arquivo(s) · {elapsed_min:.1f} min_\n\n"
        f"## 🗂 Estrutura\n\n```\n{tree}\n```\n\n---\n\n"
    )
    return header + summary