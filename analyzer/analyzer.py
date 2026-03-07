import os
import re
import json
from hardware import detect_hardware_profile, get_profile_vars

# Detecta perfil de hardware e carrega defaults
_profile = detect_hardware_profile()
_profile_vars = get_profile_vars(_profile)

# Limiar para considerar função trivial (em linhas)
TRIVIAL_LINE_THRESHOLD = int(os.getenv("TRIVIAL_LINE_THRESHOLD", _profile_vars["TRIVIAL_LINE_THRESHOLD"]))
# Máximo de funções triviais por batch
TRIVIAL_BATCH_SIZE = int(os.getenv("TRIVIAL_BATCH_SIZE", _profile_vars["TRIVIAL_BATCH_SIZE"]))

"""
Documently — Analyzer
Fluxo semântico de 3 passos por arquivo:
    1. Scan  → lista assinaturas de funções/classes
    2. Deep  → analisa cada função individualmente
    3. Synth → sintetiza as anotações em doc do arquivo
"""

import time
import requests
from pathlib import Path
from datetime import datetime

from logger    import log_info, log_ok, log_warn, log_err, log_skip, log_telemetry
from extractor import extract_functions, FunctionNode


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL       = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
MAX_TOKENS  = int(os.getenv("MAX_TOKENS_PER_CHUNK", _profile_vars["MAX_TOKENS_PER_CHUNK"]))
REQUEST_TIMEOUT = int(os.getenv("OLLAMA_REQUEST_TIMEOUT", 300))
OLLAMA_RETRIES = int(os.getenv("OLLAMA_RETRIES", 3))
MAX_NODES_PER_FILE = int(os.getenv("MAX_NODES_PER_FILE", 40))

# Parametrização de contexto e limites por etapa
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", _profile_vars["OLLAMA_NUM_CTX"]))
SCAN_NUM_PREDICT = int(os.getenv("SCAN_NUM_PREDICT", 512))
DEEP_NUM_PREDICT = int(os.getenv("DEEP_NUM_PREDICT", 1024))
SYNTH_NUM_PREDICT = int(os.getenv("SYNTH_NUM_PREDICT", 1024))
SUMMARY_NUM_PREDICT = int(os.getenv("SUMMARY_NUM_PREDICT", 1500))
CONTEXT_WINDOW_SIZE = int(os.getenv("CONTEXT_WINDOW_SIZE", 12))
CONTEXT_SNIPPET_CHARS = int(os.getenv("CONTEXT_SNIPPET_CHARS", 320))
RUNNING_CONTEXT_CHARS = int(os.getenv("RUNNING_CONTEXT_CHARS", 700))
MAX_SCAN_ITEMS = int(os.getenv("MAX_SCAN_ITEMS", 24))
MAX_DEEP_BODY_CHARS = int(os.getenv("MAX_DEEP_BODY_CHARS", 2200))
MAX_SYNTH_ITEMS = int(os.getenv("MAX_SYNTH_ITEMS", 20))
MAX_PROJECT_SUMMARY_ITEMS = int(os.getenv("MAX_PROJECT_SUMMARY_ITEMS", 40))
MAX_FUNCTION_DOC_ITEMS = int(os.getenv("MAX_FUNCTION_DOC_ITEMS", 12))
DEEP_MAX_WORDS = int(os.getenv("DEEP_MAX_WORDS", 80))
SYNTH_MAX_WORDS = int(os.getenv("SYNTH_MAX_WORDS", 260))
FALLBACK_MAX_WORDS = int(os.getenv("FALLBACK_MAX_WORDS", 180))
DEEP_CONTEXT_CHARS = int(os.getenv("DEEP_CONTEXT_CHARS", 180))
DEEP_BODY_CHAR_CAP = int(os.getenv("DEEP_BODY_CHAR_CAP", 1400))
DEEP_MIN_PREDICT = int(os.getenv("DEEP_MIN_PREDICT", 128))
DEEP_FUNCTION_PREDICT = int(os.getenv("DEEP_FUNCTION_PREDICT", 220))
DEEP_CLASS_PREDICT = int(os.getenv("DEEP_CLASS_PREDICT", 320))
PROMPT_DEBUG_LOG = os.getenv("PROMPT_DEBUG_LOG", "1").lower() not in {"0", "false", "no"}
PROMPT_LOG_MAX_CHARS = int(os.getenv("PROMPT_LOG_MAX_CHARS", 1200))
PROMPT_LOG_INCLUDE_FULL = os.getenv("PROMPT_LOG_INCLUDE_FULL", "0").lower() in {"1", "true", "yes"}
TRUNCATION_STATS_FILE = os.getenv("TRUNCATION_STATS_FILE", "truncation_stats.json")
TELEMETRY_LOG_DIR = Path(os.getenv("TELEMETRY_LOG_DIR", "/output/logs"))
EXTRACT_TARGETS_RAW = os.getenv(
    "EXTRACT_TARGETS",
    "business_rules,validations,integrations,dependencies",
)
EXTRACT_TARGETS = [x.strip() for x in EXTRACT_TARGETS_RAW.split(",") if x.strip()]

EXTRACT_TARGET_HINTS = {
    "business_rules": "business rules and outcomes",
    "validations": "validations and decision points",
    "endpoint_contracts": "endpoint contracts (method/path/params/auth/response/error)",
    "auth": "authentication and authorization requirements",
    "request_response": "request/response payload contracts",
    "error_mapping": "error handling and status mapping",
    "integrations": "external integrations and side effects",
    "dependencies": "key dependencies",
    "responsibilities": "file/module responsibilities",
    "data_flow": "data flow between components",
    "risks": "functional risks and limitations",
}

# Limites de fallback para etapas
STAGE_LIMITS = {
    "scan": SCAN_NUM_PREDICT,
    "deep": DEEP_NUM_PREDICT,
    "synth": SYNTH_NUM_PREDICT,
    "summary": SUMMARY_NUM_PREDICT,
}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _trim_middle(text: str, max_chars: int, marker: str = "\n\n[...content omitted...]\n\n") -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= len(marker) + 8:
        return text[:max_chars]
    head = (max_chars - len(marker)) // 2
    tail = max_chars - len(marker) - head
    return text[:head] + marker + text[-tail:]


def _compact_context(context_window: list[str], max_items: int = 4, max_chars: int = CONTEXT_SNIPPET_CHARS) -> str:
    if not context_window:
        return ""
    chosen = []
    seen = set()
    for entry in reversed(context_window):
        entry = entry.strip()
        if not entry:
            continue
        key = entry.split(":", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        chosen.append(entry)
        if len(chosen) >= max_items:
            break
    compact = "\n".join(reversed(chosen))
    return _trim_middle(compact, max_chars)


def _compact_scan_list(nodes: list[FunctionNode], filename: str) -> str:
    if not nodes:
        return ""
    lines = [f"- {n.name} ({n.kind}, L{n.start_line}-L{n.end_line})" for n in nodes[:MAX_SCAN_ITEMS]]
    omitted = len(nodes) - len(lines)
    if omitted > 0:
        lines.append(f"- ... +{omitted} item(ns) omitido(s)")
    return f"Detected items in {filename}:\n" + "\n".join(lines)


def _append_running_context(current: str, name: str, doc: str) -> str:
    updated = f"{current}\n[{name}]: {doc[:120]}".strip()
    if len(updated) > RUNNING_CONTEXT_CHARS:
        return updated[-RUNNING_CONTEXT_CHARS:]
    return updated


def _fit_prompt_to_budget(prompt: str, num_predict: int) -> tuple[str, int, int]:
    budget = max(256, OLLAMA_NUM_CTX - 64)
    safe_predict = max(64, min(num_predict, budget - 96))
    prompt_tokens = _estimate_tokens(prompt)
    max_prompt_tokens = max(96, budget - safe_predict)
    if prompt_tokens <= max_prompt_tokens:
        return prompt, safe_predict, prompt_tokens
    trimmed = _trim_middle(prompt, max_prompt_tokens * 4)
    return trimmed, safe_predict, _estimate_tokens(trimmed)


def _prompt_preview(prompt: str) -> str:
    preview = prompt if PROMPT_LOG_INCLUDE_FULL else _trim_middle(prompt, PROMPT_LOG_MAX_CHARS)
    return preview.replace("\n", "\\n")


def _stats_path() -> Path:
    return TELEMETRY_LOG_DIR / TRUNCATION_STATS_FILE


def _load_stats() -> dict:
    path = _stats_path()
    if not path.exists():
        return {"by_extension": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"by_extension": {}}


def _save_stats(stats: dict):
    try:
        TELEMETRY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        _stats_path().write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _record_truncation_stats(meta: dict, had_truncation: bool, resolved_after_retry: bool,
                             attempts: int, initial_predict: int, final_predict: int):
    ext = (meta.get("file_ext") or "<unknown>").lower()
    stage = meta.get("stage") or "unknown"
    stats = _load_stats()
    by_ext = stats.setdefault("by_extension", {})
    ext_item = by_ext.setdefault(ext, {
        "calls": 0,
        "had_truncation": 0,
        "resolved_after_retry": 0,
        "stages": {},
        "final_predict_hist": {},
        "best_settings": {},
    })
    ext_item["calls"] += 1
    if had_truncation:
        ext_item["had_truncation"] += 1
    if resolved_after_retry:
        ext_item["resolved_after_retry"] += 1

    stages = ext_item.setdefault("stages", {})
    st = stages.setdefault(stage, {"calls": 0, "had_truncation": 0, "resolved_after_retry": 0})
    st["calls"] += 1
    if had_truncation:
        st["had_truncation"] += 1
    if resolved_after_retry:
        st["resolved_after_retry"] += 1

    fp_key = str(final_predict)
    ext_item["final_predict_hist"][fp_key] = ext_item["final_predict_hist"].get(fp_key, 0) + 1

    if resolved_after_retry:
        setting = f"ctx={OLLAMA_NUM_CTX}|init={initial_predict}|final={final_predict}"
        ext_item["best_settings"][setting] = ext_item["best_settings"].get(setting, 0) + 1

    _save_stats(stats)


def _extraction_focus_text() -> str:
    if not EXTRACT_TARGETS:
        return ""
    hints = [EXTRACT_TARGET_HINTS.get(t, t.replace("_", " ")) for t in EXTRACT_TARGETS]
    return "Extraction scope:\n- " + "\n- ".join(hints)


def _wants_endpoint_contracts() -> bool:
    return "endpoint_contracts" in EXTRACT_TARGETS or "request_response" in EXTRACT_TARGETS


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


def call_ollama(prompt: str, num_predict: int = 1024, meta: dict | None = None) -> str:
    """
    Chama Ollama e detecta truncamento via done_reason == 'length'.
    Se truncar, retenta com prompt reduzido e num_predict dobrado.
    """
    meta = meta or {}
    max_attempts = max(2, OLLAMA_RETRIES)
    text = ""
    had_truncation = False
    initial_predict = num_predict
    for attempt in range(max_attempts):
        prompt, num_predict, prompt_eval_count = _fit_prompt_to_budget(prompt, num_predict)
        if PROMPT_DEBUG_LOG:
            log_info(
                f"prompt → stage={meta.get('stage', 'unknown')} file={meta.get('file_name', '-')} "
                f"ext={meta.get('file_ext', '-')} tok~{prompt_eval_count} num_predict={num_predict} "
                f"preview={_prompt_preview(prompt)}",
                meta.get("project", ""),
                meta.get("file_name", ""),
            )
        start = time.time()
        try:
            response = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model":  MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_ctx":     OLLAMA_NUM_CTX,
                        "temperature": 0.1,
                        "num_predict": num_predict,
                    },
                },
                timeout=REQUEST_TIMEOUT,
            )
            elapsed = time.time() - start
            response.raise_for_status()
            data      = response.json()
            text      = data.get("response", "").strip()
            truncated = data.get("done_reason") == "length"
            had_truncation = had_truncation or truncated
            eval_count = len(text) // 4  # Aproximação de tokens da resposta
            log_info(
                f"Ollama chamada: prompt_eval_count={prompt_eval_count}, eval_count={eval_count}, num_predict={num_predict}, truncated={truncated}, done_reason={data.get('done_reason')}, tempo={elapsed:.2f}s, tentativa={attempt+1}")

            log_telemetry("ollama_call", {
                "project": meta.get("project"),
                "file_name": meta.get("file_name"),
                "file_ext": meta.get("file_ext"),
                "stage": meta.get("stage"),
                "attempt": attempt + 1,
                "prompt_tokens_est": prompt_eval_count,
                "response_tokens_est": eval_count,
                "num_predict": num_predict,
                "num_ctx": OLLAMA_NUM_CTX,
                "truncated": truncated,
                "done_reason": data.get("done_reason"),
                "elapsed_sec": round(elapsed, 3),
                "prompt_preview": _prompt_preview(prompt),
            })

            if truncated and attempt < (max_attempts - 1):
                next_predict = min(num_predict + max(128, num_predict // 2), max(512, OLLAMA_NUM_CTX // 2))
                log_warn(f"resposta truncada — retentando com {next_predict} tokens")
                log_telemetry("ollama_truncation_retry", {
                    "project": meta.get("project"),
                    "file_name": meta.get("file_name"),
                    "file_ext": meta.get("file_ext"),
                    "stage": meta.get("stage"),
                    "attempt": attempt + 1,
                    "num_predict_current": num_predict,
                    "num_predict_next": next_predict,
                    "num_ctx": OLLAMA_NUM_CTX,
                })
                num_predict = next_predict
                prompt = _trim_middle(prompt, int(len(prompt) * 0.9))
                continue

            if truncated:
                text += "\n\n> ⚠️ _Análise truncada — use um modelo maior ou reduza MAX_TOKENS_PER_CHUNK._"

            _record_truncation_stats(
                meta,
                had_truncation=had_truncation,
                resolved_after_retry=(had_truncation and not truncated),
                attempts=attempt + 1,
                initial_predict=initial_predict,
                final_predict=num_predict,
            )

            return text
        except requests.RequestException as e:
            elapsed = time.time() - start
            wait_s = min(5 * (attempt + 1), 15)
            log_warn(f"falha ao chamar Ollama ({attempt + 1}/{max_attempts}): {e} — nova tentativa em {wait_s}s — tempo={elapsed:.2f}s")
            log_telemetry("ollama_error", {
                "project": meta.get("project"),
                "file_name": meta.get("file_name"),
                "file_ext": meta.get("file_ext"),
                "stage": meta.get("stage"),
                "attempt": attempt + 1,
                "num_predict": num_predict,
                "num_ctx": OLLAMA_NUM_CTX,
                "error": str(e),
                "elapsed_sec": round(elapsed, 3),
            })
            if attempt == max_attempts - 1:
                log_err(f"erro final ao chamar Ollama após {max_attempts} tentativas: {e}")
                _record_truncation_stats(
                    meta,
                    had_truncation=had_truncation,
                    resolved_after_retry=False,
                    attempts=attempt + 1,
                    initial_predict=initial_predict,
                    final_predict=num_predict,
                )
                raise RuntimeError(f"falha ao chamar Ollama após {max_attempts} tentativa(s): {e}") from e
            time.sleep(wait_s)

    return text


# ── chunk_text (mantido para resumo e fallback) ───────────────────────

def chunk_text(content: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    if not content:
        return [""]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def flush_current():
        nonlocal current, current_tokens
        if current:
            chunks.append("".join(current))
            current = []
            current_tokens = 0

    blocks = [b for b in re.split(r"\n\s*\n", content) if b.strip()]
    for block in blocks:
        piece = block + "\n\n"
        block_tokens = _estimate_tokens(piece)

        if block_tokens > max_tokens:
            raw_lines = piece.splitlines()
            if raw_lines and max(_estimate_tokens(line) for line in raw_lines) > max_tokens:
                flush_current()
                chunks.append(piece)
                continue
            lines = piece.splitlines(keepends=True)
            for line in lines:
                line_tokens = _estimate_tokens(line)
                if current and current_tokens + line_tokens > max_tokens:
                    flush_current()
                current.append(line)
                current_tokens += line_tokens
            continue

        if current and current_tokens + block_tokens > max_tokens:
            flush_current()
        current.append(piece)
        current_tokens += block_tokens

    flush_current()
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

def _step_scan(nodes: list[FunctionNode], filename: str, lang_label: str,
               project: str = "", file_ext: str = "") -> str:
    """
    Passo 1 — Scan rápido: lista assinaturas e pede ao modelo
    uma linha de descrição para cada função.
    """
    scan_list = _compact_scan_list(nodes, filename)
    prompt = (
        f"Analyze {lang_label} items with business-rule focus.\n\n"
        f"{scan_list}\n\n"
        f"{_extraction_focus_text()}\n\n"
        f"Return ONE line per item as: name — functional role. "
        f"No intro, no extra text. Return in English."
    )
    return call_ollama(prompt, num_predict=SCAN_NUM_PREDICT, meta={
        "stage": "scan",
        "project": project,
        "file_name": filename,
        "file_ext": file_ext,
    })


def _step_deep(node: FunctionNode, context: str, lang_label: str,
               project: str = "", file_name: str = "", file_ext: str = "") -> str:
    """
    Passo 2 — Deep dive: analisa o corpo de uma função individualmente.
    """
    target_cap = DEEP_CLASS_PREDICT if node.kind == "class" else DEEP_FUNCTION_PREDICT
    estimated = max(DEEP_MIN_PREDICT, len(node.body) // 12)
    num_predict = min(DEEP_NUM_PREDICT, target_cap, estimated)
    body_limit = min(MAX_DEEP_BODY_CHARS, DEEP_BODY_CHAR_CAP)
    context_limit = min(CONTEXT_SNIPPET_CHARS, DEEP_CONTEXT_CHARS)
    body = _trim_middle(node.body, body_limit)
    ctx = _trim_middle(context, context_limit)
    endpoint_line = (
        "- endpoint contract details if present: method/path/params/auth/response/error\n"
        if _wants_endpoint_contracts()
        else ""
    )
    prompt = (
        f"Analyze this {node.kind} in {lang_label}.\n\n"
        f"Short context:\n{ctx}\n\n"
        f"Code:\n```\n{body}\n```\n\n"
        f"{_extraction_focus_text()}\n\n"
        f"Answer in up to {DEEP_MAX_WORDS} words with functional focus (not line-by-line):\n"
        f"- impacted business rule (or 'none relevant')\n"
        f"- business input/output\n"
        f"- key decision/validation\n"
        f"{endpoint_line}"
        f"Keep terse, no examples, no repetition. Return in English."
    )
    return call_ollama(prompt, num_predict=num_predict, meta={
        "stage": "deep",
        "project": project,
        "file_name": file_name,
        "file_ext": file_ext,
        "symbol": node.name,
        "symbol_kind": node.kind,
    })


def _step_synth(annotations: list[dict], filename: str,
                lang_label: str, profile: dict,
                project: str = "", file_ext: str = "") -> str:
    """
    Passo 3 — Síntese: gera o doc completo do arquivo a partir das anotações.
    """
    compact = annotations[:MAX_SYNTH_ITEMS]
    annot_text = "\n\n".join([
        f"### `{a['name']}` ({a['kind']})\n{_trim_middle(a['doc'], 260)}"
        for a in compact
    ])
    if len(annotations) > len(compact):
        annot_text += f"\n\n... +{len(annotations) - len(compact)} item(ns) omitido(s)."
    prompt = (
        f"Synthesize {lang_label} documentation for {filename}.\n\n"
        f"Notes:\n\n"
        f"{annot_text[:5000]}\n\n"
        f"{profile['prompt_focus']}\n\n"
        f"{_extraction_focus_text()}\n\n"
        f"Generate concise docs focused on business rules and responsibilities. "
        f"Avoid line-by-line implementation details.\n"
        f"Include only:\n"
        f"- file functional purpose\n"
        f"- main business rules/validations\n"
        f"- inputs/outputs and external integrations\n"
        f"- functional risks/limitations\n"
        f"Max {SYNTH_MAX_WORDS} words. Return in English."
    )
    return call_ollama(prompt, num_predict=SYNTH_NUM_PREDICT, meta={
        "stage": "synth",
        "project": project,
        "file_name": filename,
        "file_ext": file_ext,
    })


def analyze_file(filepath: Path, project_path: Path, context_window: list,
                 status_files: dict, profile: dict, project: str,
                 docs_dir: Path) -> dict | None:
    """
    Analisa um arquivo usando o fluxo semântico de 3 passos.
    Fallback para chunk_text se tree-sitter não encontrar nenhuma função.
    """
    rel   = str(filepath)
    fname = filepath.name
    file_ext = filepath.suffix.lower() or "<none>"
    file_status = status_files.get(rel, {"done": False, "steps": {}})
    if not file_status.get("started_at"):
        file_status["started_at"] = datetime.now().isoformat()
    status_files[rel] = file_status

    start_file = time.time()
    try:
        content = filepath.read_text(errors="replace")
    except Exception as e:
        elapsed_file = time.time() - start_file
        file_status["done"] = False
        file_status["error"] = str(e)
        file_status["finished_at"] = datetime.now().isoformat()
        file_status["elapsed_sec"] = round(elapsed_file, 3)
        status_files[rel] = file_status
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
    if len(nodes) > MAX_NODES_PER_FILE:
        log_warn(
            f"{len(nodes)} nós detectados; limitando para {MAX_NODES_PER_FILE} para evitar timeout",
            project,
            fname,
        )
        nodes = nodes[:MAX_NODES_PER_FILE]

    doc_parts    = [f"# 📄 `{fname}`\n\n_Profile: {lang_label}_\n"]
    full_analysis = []
    context_summary = _compact_context(context_window)

    # Log início da análise do arquivo
    log_info(f"início da análise do arquivo: {fname}", project, fname)

    if not nodes:
        # Fallback: arquivo sem funções detectadas (config, types, etc.)
        log_info(f"sem funções detectadas — análise direta", project, fname)
        chunks = chunk_text(content)
        for i, chunk in enumerate(chunks):
            log_info(f"chunk {i+1}/{len(chunks)} → Ollama...", project, fname)
            num_predict = max(256, min(_estimate_tokens(chunk) // 2, 1536))
            prompt = (
                f"Analyze file {fname} ({lang_label}).\n\n"
                f"Short context: {context_summary}\n\n"
                f"File: {fname}\n\n```\n{chunk}\n```\n\n"
                f"{profile['prompt_focus']}\n\n"
                f"{_extraction_focus_text()}\n\n"
                f"Document from a functional business-rule perspective, no line-by-line detail. "
                f"Max {FALLBACK_MAX_WORDS} words. Return in English."
            )
            analysis = call_ollama(prompt, num_predict=num_predict, meta={
                "stage": "fallback_chunk",
                "project": project,
                "file_name": fname,
                "file_ext": file_ext,
                "chunk_index": i + 1,
                "chunk_total": len(chunks),
            })
            doc_parts.append(f"## Análise\n\n{analysis}\n")
            full_analysis.append(analysis)
        if full_analysis:
            context_window.append(f"[{fname}]: {_trim_middle(' '.join(full_analysis), 160)}")
            if len(context_window) > CONTEXT_WINDOW_SIZE:
                context_window.pop(0)
    else:
        log_info(f"{len(nodes)} função(ões)/classe(s) encontrada(s)", project, fname)

        # ── Passo 1: Scan ─────────────────────────────────────────────
        log_info("passo 1/3 — scan de assinaturas", project, fname)
        scan_result = _step_scan(nodes, fname, lang_label, project=project, file_ext=file_ext)
        doc_parts.append(f"## Function Overview\n\n{scan_result}\n")

        # ── Passo 2: Deep dive por função ─────────────────────────────
        log_info("passo 2/3 — análise individual", project, fname)
        annotations = []
        running_ctx = context_summary

        for node in nodes:
            log_info(f"  analisando `{node.name}` (L{node.start_line}–{node.end_line})", project, fname)
            doc = _step_deep(node, running_ctx, lang_label, project=project, file_name=fname, file_ext=file_ext)
            annotations.append({"name": node.name, "kind": node.kind, "doc": doc})
            running_ctx = _append_running_context(running_ctx, node.name, doc)

        visible_annotations = annotations[:MAX_FUNCTION_DOC_ITEMS]
        annot_section = "\n\n".join([
            f"### `{a['name']}` ({a['kind']})\n{_trim_middle(a['doc'], 220)}"
            for a in visible_annotations
        ])
        if len(annotations) > len(visible_annotations):
            annot_section += (
                f"\n\n_+{len(annotations) - len(visible_annotations)} function(s)/class(es) "
                f"omitted to keep functional focus._"
            )
        doc_parts.append(f"## Function Notes\n\n{annot_section}\n")

        # ── Passo 3: Síntese ──────────────────────────────────────────
        log_info("passo 3/3 — síntese do arquivo", project, fname)
        synth = _step_synth(annotations, fname, lang_label, profile, project=project, file_ext=file_ext)
        doc_parts.append(f"## File Summary\n\n{synth}\n")
        full_analysis = [a["doc"] for a in annotations] + [synth]

        # Atualiza janela de contexto com síntese
        context_window.append(f"[{fname}]: {synth[:160]}")
        if len(context_window) > CONTEXT_WINDOW_SIZE:
            context_window.pop(0)

    # ── Salva doc espelhando estrutura de pastas ──────────────────────
    try:
        relative_path = filepath.relative_to(project_path)
        doc_path      = docs_dir / project / relative_path.with_suffix(".md")
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text("\n".join(doc_parts), encoding="utf-8")
        elapsed_file = time.time() - start_file

        file_status["done"]     = True
        file_status["error"]    = None
        file_status["doc_path"] = str(doc_path)
        file_status["finished_at"] = datetime.now().isoformat()
        file_status["elapsed_sec"] = round(elapsed_file, 3)
        status_files[rel] = file_status

        log_info(f"fim da análise do arquivo: {fname} — tempo total={elapsed_file:.2f}s", project, fname)

        log_ok(f"salvo → {doc_path.relative_to(docs_dir)}", project, fname)
        return {"path": str(relative_path), "analysis": " ".join(full_analysis)}
    except Exception as e:
        elapsed_file = time.time() - start_file
        file_status["done"]  = False
        file_status["error"] = str(e)
        file_status["finished_at"] = datetime.now().isoformat()
        file_status["elapsed_sec"] = round(elapsed_file, 3)
        status_files[rel] = file_status
        log_err(f"erro ao salvar/analisar arquivo: {e}", project, fname)
        return None


# ── Resumo do projeto ─────────────────────────────────────────────────

def generate_summary(project_name: str, project_path: Path, profile: dict,
                     file_docs: list[dict], elapsed_min: float, framework: str = None) -> str:
    log_info("gerando resumo geral...", project_name)
    tree    = build_tree(project_path, profile)
    summary_items = [
        f"- {d['path']}: {_trim_middle(d['analysis'], 180)}"
        for d in file_docs[:MAX_PROJECT_SUMMARY_ITEMS]
    ]
    if len(file_docs) > len(summary_items):
        summary_items.append(f"- ... +{len(file_docs) - len(summary_items)} arquivo(s) omitido(s)")
    context = "\n".join(summary_items)
    endpoint_section = "## Endpoint Contracts\n" if _wants_endpoint_contracts() else ""
    prompt = (
        f"You are a senior software architect.\n\n"
        f"Project: {project_name}\n"
        f"Profile: {profile['lang_label']}\n"
        f"Framework: {framework if framework else 'unknown'}\n"
        f"Analyzed files: {len(file_docs)}\n"
        f"Elapsed: {elapsed_min:.1f} min\n\n"
        f"Structure:\n```\n{tree}\n```\n\n"
        f"Per-file summary:\n{context[:4200]}\n\n"
        f"Selected extraction scope: {', '.join(EXTRACT_TARGETS) if EXTRACT_TARGETS else 'default'}\n\n"
        f"Generate an English technical report focused on business rules (avoid line-by-line):\n"
        f"## Overview\n"
        f"## Architecture\n"
        f"## Main Business Rules\n"
        f"{endpoint_section}"
        f"## Files by Responsibility\n"
        f"## Key Dependencies\n"
        f"## Risks and Attention Points\n\n"
        f"Be concise and technical."
    )

    summary = call_ollama(prompt, num_predict=SUMMARY_NUM_PREDICT, meta={
        "stage": "project_summary",
        "project": project_name,
        "file_name": "_resumo.md",
        "file_ext": ".md",
    })
    header  = (
        f"# 📋 Summary: `{project_name}`\n\n"
        f"_Generated at {datetime.now().strftime('%d/%m/%Y %H:%M')} · "
        f"Profile: **{profile['lang_label']}** · "
        f"Framework: **{framework if framework else 'unknown'}** · "
        f"{len(file_docs)} file(s) · {elapsed_min:.1f} min_\n\n"
        f"## 🗂 Structure\n\n```\n{tree}\n```\n\n---\n\n"
        f"## 🎯 Extraction Scope\n\n"
        f"{', '.join(EXTRACT_TARGETS) if EXTRACT_TARGETS else 'default'}\n\n"
    )
    return header + summary