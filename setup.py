"""
Documently — Setup interativo
Detecta hardware, recomenda modelo e gera .env para o docker-compose.
Funciona em Linux, Mac e Windows 10/11.

Flags:
  --reset   Para tudo, limpa imagens/volumes/cache e reconstrói do zero
  --help    Mostra esta ajuda
"""

import os
import sys
import shutil
import platform
import subprocess
from pathlib import Path

# ── Cores ANSI ────────────────────────────────────────────────────────
def supports_color() -> bool:
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except Exception:
            return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

USE_COLOR = supports_color()

class C:
    RESET  = "\033[0m"   if USE_COLOR else ""
    BOLD   = "\033[1m"   if USE_COLOR else ""
    DIM    = "\033[2m"   if USE_COLOR else ""
    WHITE  = "\033[97m"  if USE_COLOR else ""
    GRAY   = "\033[90m"  if USE_COLOR else ""
    GREEN  = "\033[92m"  if USE_COLOR else ""
    YELLOW = "\033[93m"  if USE_COLOR else ""
    RED    = "\033[91m"  if USE_COLOR else ""
    CYAN   = "\033[96m"  if USE_COLOR else ""
    PURPLE = "\033[95m"  if USE_COLOR else ""

def success(msg):   print(f"{C.GREEN}  ✅ {msg}{C.RESET}")
def warn(msg):      print(f"{C.YELLOW}  ⚠️  {msg}{C.RESET}")
def error(msg):     print(f"{C.RED}  ❌ {msg}{C.RESET}")
def info(msg):      print(f"{C.CYAN}  ℹ️  {msg}{C.RESET}")
def step(msg):      print(f"\n{C.BOLD}{C.WHITE}{msg}{C.RESET}")
def dim(msg):       print(f"{C.GRAY}{msg}{C.RESET}")
def highlight(msg): print(f"{C.PURPLE}{C.BOLD}{msg}{C.RESET}")


# ── Detecção de hardware ──────────────────────────────────────────────

def get_ram_gb() -> int:
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        return int(line.split()[1]) // 1024 // 1024
        elif system == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode()
            return int(out.strip()) // 1024 // 1024 // 1024
        elif system == "Windows":
            out = subprocess.check_output(
                ["wmic", "computersystem", "get", "TotalPhysicalMemory"],
                stderr=subprocess.DEVNULL
            ).decode()
            for line in out.splitlines():
                line = line.strip()
                if line.isdigit():
                    return int(line) // 1024 // 1024 // 1024
    except Exception:
        pass
    return 0


def get_gpu_info() -> dict:
    """Detecta GPU Nvidia (nvidia-smi), AMD (rocm-smi/rocminfo) ou Windows WMI."""

    # ── Nvidia ────────────────────────────────────────────────────────
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            if out:
                parts = out.split(",")
                return {
                    "found":   True,
                    "vendor":  "nvidia",
                    "name":    parts[0].strip(),
                    "vram_gb": round(int(parts[1].strip()) / 1024, 1),
                }
        except Exception:
            pass

    # ── AMD (rocm-smi) ────────────────────────────────────────────────
    if shutil.which("rocm-smi"):
        try:
            out = subprocess.check_output(
                ["rocm-smi", "--showmeminfo", "vram", "--csv"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            # Formato: GPU[0], VRAM Total Memory (B), 8589934592
            vram_bytes = 0
            name       = "AMD GPU"
            for line in out.splitlines():
                if "VRAM Total Memory" in line:
                    parts = line.split(",")
                    if len(parts) >= 3:
                        vram_bytes = int(parts[-1].strip())
                        break
            if vram_bytes:
                return {
                    "found":   True,
                    "vendor":  "amd",
                    "name":    name,
                    "vram_gb": round(vram_bytes / 1024 / 1024 / 1024, 1),
                }
        except Exception:
            pass

    # ── AMD (rocminfo — fallback) ─────────────────────────────────────
    if shutil.which("rocminfo"):
        try:
            out = subprocess.check_output(
                ["rocminfo"], stderr=subprocess.DEVNULL
            ).decode()
            vram_gb = 0.0
            name    = "AMD GPU"
            for line in out.splitlines():
                line = line.strip()
                if "Marketing Name" in line:
                    name = line.split(":", 1)[-1].strip()
                if "Global Memory Size" in line:
                    # Ex: "Global Memory Size:          8176( M)"
                    import re
                    m = re.search(r'(\d+)\s*\(\s*M\s*\)', line)
                    if m:
                        vram_gb = round(int(m.group(1)) / 1024, 1)
            if vram_gb:
                return {
                    "found":   True,
                    "vendor":  "amd",
                    "name":    name,
                    "vram_gb": vram_gb,
                }
        except Exception:
            pass

    # ── Windows WMI (Nvidia ou AMD sem drivers ROCm) ──────────────────
    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(
                ["wmic", "path", "win32_VideoController",
                 "get", "Name,AdapterRAM", "/format:csv"],
                stderr=subprocess.DEVNULL
            ).decode(errors="ignore").strip()
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 3:
                    continue
                name     = parts[2]
                ram_str  = parts[1]
                name_low = name.lower()
                if not ram_str.isdigit():
                    continue
                vram_gb = round(int(ram_str) / 1024 / 1024 / 1024, 1)
                if vram_gb < 0.5:
                    continue  # ignora GPUs integradas fracas
                if "nvidia" in name_low:
                    vendor = "nvidia"
                elif "amd" in name_low or "radeon" in name_low:
                    vendor = "amd"
                elif "intel" in name_low:
                    vendor = "intel"
                else:
                    vendor = "unknown"
                if vendor in ("nvidia", "amd"):
                    return {
                        "found":   True,
                        "vendor":  vendor,
                        "name":    name,
                        "vram_gb": vram_gb,
                    }
        except Exception:
            pass

    return {"found": False, "vendor": None}


# ── Modelos disponíveis ───────────────────────────────────────────────

MODELS = [
    {
        "id": "qwen2.5-coder:3b",
        "label": "Qwen 2.5 Coder 3B",
        "size_gb": 1.9,
        "min_vram": 0,
        "min_ram": 8,
        "quality": "boa",
        "speed": "rápida",
        "best_for": "uso geral, máquinas modestas, CPU ok",
    },
    {
        "id": "qwen2.5-coder:7b",
        "label": "Qwen 2.5 Coder 7B",
        "size_gb": 4.7,
        "min_vram": 4,
        "min_ram": 12,
        "quality": "ótima",
        "speed": "média",
        "best_for": "melhor custo-benefício, JS/TS/Python",
    },
    {
        "id": "deepseek-coder-v2:16b",
        "label": "DeepSeek Coder V2 16B",
        "size_gb": 8.9,
        "min_vram": 8,
        "min_ram": 16,
        "quality": "excelente",
        "speed": "média",
        "best_for": "análise detalhada, contratos Solidity, Java",
    },
    {
        "id": "qwen2.5-coder:14b",
        "label": "Qwen 2.5 Coder 14B",
        "size_gb": 9.0,
        "min_vram": 8,
        "min_ram": 16,
        "quality": "excelente",
        "speed": "lenta",
        "best_for": "projetos grandes, múltiplas linguagens",
    },
    {
        "id": "qwen2.5-coder:32b",
        "label": "Qwen 2.5 Coder 32B",
        "size_gb": 19.0,
        "min_vram": 20,
        "min_ram": 32,
        "quality": "state-of-the-art",
        "speed": "lenta",
        "best_for": "máxima qualidade, GPUs de alto desempenho",
    },
]

EXTRACTION_PRESETS = {
    "1": {
        "label": "Business rules (recommended)",
        "targets": "business_rules,validations,integrations,dependencies",
    },
    "2": {
        "label": "Endpoint contracts",
        "targets": "endpoint_contracts,auth,request_response,error_mapping",
    },
    "3": {
        "label": "Architecture overview",
        "targets": "responsibilities,dependencies,data_flow,risks",
    },
    "4": {
        "label": "Everything (broader, costs more)",
        "targets": "business_rules,validations,endpoint_contracts,auth,request_response,error_mapping,integrations,dependencies,responsibilities,data_flow,risks",
    },
}


def recommend_model(ram_gb: int, gpu: dict) -> dict:
    vram = gpu["vram_gb"] if gpu["found"] else 0
    candidates = [
        m for m in MODELS
        if ram_gb >= m["min_ram"] and (
            (gpu["found"] and vram >= m["min_vram"])
            or (not gpu["found"] and m["min_vram"] == 0)
        )
    ]
    return candidates[-1] if candidates else MODELS[0]


# ── Interface interativa ──────────────────────────────────────────────

def ask(prompt: str, default: str = "") -> str:
    default_hint = f"{C.DIM} [{default}]{C.RESET}" if default else ""
    try:
        answer = input(f"{C.BOLD}{C.WHITE}  {prompt}{default_hint}: {C.RESET}").strip()
        return answer if answer else default
    except (KeyboardInterrupt, EOFError):
        print("\n")
        sys.exit(0)


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint   = "S/n" if default else "s/N"
    answer = ask(f"{prompt} ({hint})", "s" if default else "n")
    return answer.lower() in ("s", "sim", "y", "yes", "")


def choose_model(current: dict) -> dict:
    print(f"\n{C.BOLD}  Modelos disponíveis:{C.RESET}\n")
    for i, m in enumerate(MODELS):
        marker = f"{C.GREEN}▶ {C.RESET}" if m["id"] == current["id"] else "  "
        print(
            f"  {marker}{C.BOLD}{C.WHITE}[{i+1}]{C.RESET} "
            f"{C.CYAN}{m['label']}{C.RESET}\n"
            f"      {C.GRAY}Tamanho: ~{m['size_gb']}GB  |  "
            f"Qualidade: {m['quality']}  |  Velocidade: {m['speed']}{C.RESET}\n"
            f"      {C.DIM}Ideal para: {m['best_for']}{C.RESET}\n"
        )
    choice = ask(
        f"Escolha [1-{len(MODELS)}] ou Enter para manter o recomendado",
        str(MODELS.index(current) + 1)
    )
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(MODELS):
            return MODELS[idx]
    except ValueError:
        pass
    return current


def choose_extraction_targets() -> str:
    print(f"\n{C.BOLD}  Escopo de extração:{C.RESET}\n")
    for key, item in EXTRACTION_PRESETS.items():
        print(f"  {C.BOLD}{C.WHITE}[{key}]{C.RESET} {item['label']}")
        print(f"      {C.DIM}{item['targets']}{C.RESET}")
    print(f"  {C.BOLD}{C.WHITE}[5]{C.RESET} Custom (lista separada por vírgula)\n")

    choice = ask("Escolha", "1")
    if choice in EXTRACTION_PRESETS:
        return EXTRACTION_PRESETS[choice]["targets"]
    if choice == "5":
        custom = ask(
            "Informe os targets (ex: business_rules,endpoint_contracts,integrations)",
            EXTRACTION_PRESETS["1"]["targets"],
        )
        parts = [x.strip() for x in custom.split(",") if x.strip()]
        return ",".join(parts) if parts else EXTRACTION_PRESETS["1"]["targets"]
    return EXTRACTION_PRESETS["1"]["targets"]


# ── Geração do .env ───────────────────────────────────────────────────

def generate_env(model: dict, gpu: dict, extraction_targets: str | None = None) -> dict:
    gpu_layers = 0
    if gpu["found"]:
        # AMD ROCm e Nvidia CUDA usam o mesmo parâmetro no Ollama
        gpu_layers = min(35, int((gpu["vram_gb"] * 1024) / 200))
    return {
        "OLLAMA_MODEL":           model["id"],
        "MAX_TOKENS_PER_CHUNK":   "3000",
        "EXTENSIONS":             ".sol,.py,.js,.ts,.go,.rs,.java",
        "OLLAMA_NUM_GPU_LAYERS":  str(gpu_layers),
        "OLLAMA_NUM_PARALLEL":    "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        # Novas variáveis para contexto e limites por etapa
        "OLLAMA_NUM_CTX":         "4096",
        "SCAN_NUM_PREDICT":      "512",
        "DEEP_NUM_PREDICT":      "1024",
        "SYNTH_NUM_PREDICT":     "1024",
        "SUMMARY_NUM_PREDICT":   "1500",
        "MAX_FUNCTION_DOC_ITEMS": "12",
        "DEEP_MAX_WORDS":        "80",
        "SYNTH_MAX_WORDS":       "260",
        "FALLBACK_MAX_WORDS":    "180",
        "DEEP_CONTEXT_CHARS":    "180",
        "DEEP_BODY_CHAR_CAP":    "1400",
        "DEEP_MIN_PREDICT":      "128",
        "DEEP_FUNCTION_PREDICT": "220",
        "DEEP_CLASS_PREDICT":    "320",
        "EXTRACT_TARGETS":       extraction_targets or EXTRACTION_PRESETS["1"]["targets"],
        "HARDWARE_PROFILE":      "mid",
        "TELEMETRY_ENABLED":     "1",
        "TELEMETRY_LOG_DIR":     "/output/logs",
        "TELEMETRY_LOG_FILE":    "ollama_telemetry.jsonl",
        "TRUNCATION_STATS_FILE": "truncation_stats.json",
        "PROMPT_DEBUG_LOG":      "1",
        "PROMPT_LOG_MAX_CHARS":  "1200",
        "PROMPT_LOG_INCLUDE_FULL": "0",
    }



def patch_compose(gpu: dict):
    """
    Ajusta o docker-compose.yml para usar o bloco de GPU correto:
    - Nvidia → deploy.resources (CUDA)
    - AMD    → devices /dev/kfd + /dev/dri (ROCm)
    - CPU    → nenhum bloco de GPU
    """
    compose_path = Path("docker-compose.yml")
    if not compose_path.exists():
        warn("docker-compose.yml não encontrado — pulando patch de GPU")
        return

    content = compose_path.read_text(encoding="utf-8")
    vendor  = gpu.get("vendor") if gpu.get("found") else None

    # Remove todos os blocos de GPU existentes para reinserir o correto
    import re

    # Bloco Nvidia
    nvidia_block = (
        "    deploy:\n"
        "      resources:\n"
        "        reservations:\n"
        "          devices:\n"
        "            - driver: nvidia\n"
        "              count: all\n"
        "              capabilities: [gpu]\n"
    )
    # Bloco AMD (comentado → ativo)
    amd_devices = (
        "    devices:\n"
        "      - /dev/kfd:/dev/kfd\n"
        "      - /dev/dri:/dev/dri\n"
        "    group_add:\n"
        "      - video\n"
        "      - render\n"
    )

    if vendor == "nvidia":
        info("Configurando docker-compose.yml para GPU Nvidia (CUDA)")
        # Garante que o bloco deploy está presente e AMD está comentado
        if "deploy:" not in content:
            content = content.replace(
                "    env_file: .env\n    environment:\n      - OLLAMA_NUM_PARALLEL",
                "    env_file: .env\n" + nvidia_block + "    environment:\n      - OLLAMA_NUM_PARALLEL",
            )
        success("docker-compose.yml → modo Nvidia CUDA")

    elif vendor == "amd":
        info("Configurando docker-compose.yml para GPU AMD (ROCm)")
        # Remove bloco nvidia se existir, adiciona bloco AMD
        content = re.sub(
            r"    deploy:.*?capabilities: \[gpu\]\n",
            "", content, flags=re.DOTALL
        )
        if "/dev/kfd" not in content:
            content = content.replace(
                "    env_file: .env\n    environment:",
                "    env_file: .env\n" + amd_devices + "    environment:",
            )
        success("docker-compose.yml → modo AMD ROCm")
        warn("Certifique-se de ter o ROCm instalado: https://rocm.docs.amd.com")

    else:
        info("Sem GPU dedicada — docker-compose.yml em modo CPU")
        # Remove blocos de GPU
        content = re.sub(
            r"    deploy:.*?capabilities: \[gpu\]\n",
            "", content, flags=re.DOTALL
        )
        content = re.sub(
            r"    devices:\n.*?/dev/dri.*?\n(    group_add:.*?render\n)?",
            "", content, flags=re.DOTALL
        )
        success("docker-compose.yml → modo CPU only")

    compose_path.write_text(content, encoding="utf-8")

def write_env(config: dict):
    # Detecta UID/GID do usuário atual para o container não criar arquivos como root
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    lines = [
        "# Gerado pelo setup.py do Documently",
        "# Para reconfigurar: python3 setup.py\n",
        f"DOCKER_UID={uid}",
        f"DOCKER_GID={gid}",
        "",
        "# Limites de contexto e tokens por etapa (ajuste conforme necessário)",
        "# HARDWARE_PROFILE: low, mid, high, ultra (auto ou override)",
        "# OLLAMA_NUM_CTX: tamanho máximo do contexto (tokens) por chamada",
        "# SCAN_NUM_PREDICT: tokens para etapa de scan (assinaturas)",
        "# DEEP_NUM_PREDICT: tokens para deep dive (função/classe)",
        "# SYNTH_NUM_PREDICT: tokens para síntese do arquivo",
        "# SUMMARY_NUM_PREDICT: tokens para resumo do projeto",
        "# MAX_FUNCTION_DOC_ITEMS: máximo de funções detalhadas por arquivo na seção por função",
        "# DEEP_MAX_WORDS: limite de palavras por função/classe no deep dive",
        "# DEEP_CONTEXT_CHARS: limite de contexto injetado no deep dive",
        "# DEEP_BODY_CHAR_CAP: limite efetivo de código enviado ao deep dive",
        "# DEEP_MIN_PREDICT: piso de tokens no deep dive",
        "# DEEP_FUNCTION_PREDICT: teto de tokens para funções no deep dive",
        "# DEEP_CLASS_PREDICT: teto de tokens para classes no deep dive",
        "# EXTRACT_TARGETS: define o escopo funcional/contratos que devem ser extraídos",
        "# SYNTH_MAX_WORDS: limite de palavras no resumo funcional por arquivo",
        "# FALLBACK_MAX_WORDS: limite de palavras na análise de arquivos sem funções",
        "# TELEMETRY_ENABLED: 1 para persistir telemetria JSONL de chamadas Ollama",
        "# TELEMETRY_LOG_DIR: diretório de logs dentro do container (montado para ./logs)",
        "# TRUNCATION_STATS_FILE: agregados por extensão/etapa para tuning",
        "# PROMPT_DEBUG_LOG: log de preview do prompt no stdout do container",
        "# PROMPT_LOG_INCLUDE_FULL: 1 para logar prompt completo (alto volume)",
        "",
    ]
    for key, value in config.items():
        lines.append(f"{key}={value}")
    Path(".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Reset ─────────────────────────────────────────────────────────────

def run_cmd(cmd: list[str], label: str):
    info(f"{label}...")
    try:
        subprocess.run(cmd, check=True)
        success(label)
    except subprocess.CalledProcessError as e:
        warn(f"{label} retornou erro (pode ser normal): {e}")
    except FileNotFoundError:
        warn(f"comando não encontrado: {cmd[0]}")


def do_reset(clean_docs: bool = True):
    os.system("cls" if platform.system() == "Windows" else "clear")
    print(f"\n{C.BOLD}{C.RED}{'─' * 50}{C.RESET}")
    highlight("   🔄 Documently — Reset completo")
    print(f"{C.BOLD}{C.RED}{'─' * 50}{C.RESET}\n")
    warn("Isso vai parar os containers, apagar volumes e cache de build.")

    if not ask_yes_no("Confirmar reset?", default=False):
        info("Reset cancelado.")
        return

    step("1 / 4  Parando containers e apagando volumes...")
    run_cmd(["docker", "compose", "down", "--volumes", "--remove-orphans"],
            "docker compose down --volumes")

    step("2 / 4  Removendo imagens do projeto...")
    run_cmd(["docker", "rmi", "documently-analyzer", "--force"],
            "remoção da imagem documently-analyzer")

    step("3 / 4  Limpando cache de build...")
    run_cmd(["docker", "builder", "prune", "-f"], "docker builder prune")

    if clean_docs:
        step("4 / 4  Limpando docs e status gerados...")
        for folder in ["docs", "status"]:
            p = Path(folder)
            if not p.exists():
                dim(f"  pasta {folder}/ não encontrada, pulando")
                continue
            try:
                shutil.rmtree(p)
                success(f"pasta {folder}/ removida")
            except PermissionError:
                # Arquivos criados pelo Docker rodam como root
                warn(f"permissão negada em {folder}/ — tentando com sudo...")
                try:
                    subprocess.run(["sudo", "rm", "-rf", str(p)], check=True)
                    success(f"pasta {folder}/ removida (via sudo)")
                except subprocess.CalledProcessError:
                    error(
                        f"Não foi possível remover {folder}/. "
                        f"Rode manualmente: sudo rm -rf {folder}/"
                    )
    else:
        step("4 / 4  Mantendo docs e status (pulado)")

    print(f"\n{C.GREEN}{C.BOLD}  ✅ Reset concluído!{C.RESET}")
    info("Rode agora:  python3 setup.py  para reconfigurar e rebuildar.")
    print()


# ── Setup principal ───────────────────────────────────────────────────

def main():
    # ── Flags de linha de comando ─────────────────────────────────────
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    if "--reset" in args:
        keep_docs = "--keep-docs" in args
        do_reset(clean_docs=not keep_docs)
        if not ask_yes_no("\nIniciar setup agora?", default=True):
            sys.exit(0)

    os.system("cls" if platform.system() == "Windows" else "clear")

    print(f"\n{C.BOLD}{C.PURPLE}{'─' * 50}{C.RESET}")
    highlight("   🔍 Documently — Setup")
    print(f"{C.BOLD}{C.PURPLE}{'─' * 50}{C.RESET}\n")
    dim("   Vamos configurar o ambiente para o seu hardware.\n")

    # ── Menu de opções extras ─────────────────────────────────────────
    print(f"  {C.BOLD}Opções:{C.RESET}")
    print(f"  {C.WHITE}[1]{C.RESET} Configuração normal")
    print(f"  {C.WHITE}[2]{C.RESET} Reset completo (limpa containers, imagens, cache, docs)")
    print(f"  {C.WHITE}[3]{C.RESET} Reset parcial (limpa containers e cache, mantém docs)\n")
    choice = ask("Escolha", "1")

    if choice == "2":
        do_reset(clean_docs=True)
        if not ask_yes_no("Continuar com o setup?", default=True):
            sys.exit(0)
    elif choice == "3":
        do_reset(clean_docs=False)
        if not ask_yes_no("Continuar com o setup?", default=True):
            sys.exit(0)

    # ── 1. Detecta hardware ───────────────────────────────────────────
    step("1 / 4  Detectando hardware...")

    ram_gb = get_ram_gb()
    gpu    = get_gpu_info()

    if ram_gb:
        success(f"RAM detectada: {C.CYAN}{ram_gb} GB{C.RESET}")
    else:
        warn("Não foi possível detectar a RAM.")
        ram_gb = int(ask("Digite a RAM total em GB", "16"))

    if gpu["found"]:
        vendor_label = {"nvidia": "Nvidia", "amd": "AMD", "intel": "Intel"}.get(gpu.get("vendor", ""), "GPU")
        success(
            f"{vendor_label} detectada: {C.CYAN}{gpu['name']}{C.RESET} "
            f"com {C.CYAN}{gpu['vram_gb']} GB{C.RESET} de VRAM"
        )
        if gpu.get("vendor") == "amd":
            info("GPU AMD detectada — certifique-se de ter o ROCm instalado para aceleração GPU.")
            info("Sem ROCm, o Ollama usará a CPU. Mais info: https://rocm.docs.amd.com")
    else:
        warn("Nenhuma GPU dedicada detectada — Ollama usará a CPU.")
        if ask_yes_no("Você tem GPU (Nvidia ou AMD) mas os drivers não foram detectados?", default=False):
            vram   = float(ask("Quantos GB de VRAM?", "4"))
            name   = ask("Nome da GPU (ex: RTX 3060 / RX 6700 XT)", "GPU")
            vendor = "amd" if any(x in name.lower() for x in ["amd", "rx", "radeon", "vega"]) else "nvidia"
            gpu    = {"found": True, "vendor": vendor, "name": name, "vram_gb": vram}

    # ── 2. Recomenda modelo ───────────────────────────────────────────
    step("2 / 4  Recomendando modelo...")

    recommended = recommend_model(ram_gb, gpu)
    print(f"\n  {C.BOLD}Modelo recomendado:{C.RESET}")
    print(
        f"  {C.GREEN}▶ {C.CYAN}{C.BOLD}{recommended['label']}{C.RESET}\n"
        f"  {C.GRAY}  Tamanho: ~{recommended['size_gb']}GB  |  "
        f"Qualidade: {recommended['quality']}  |  Velocidade: {recommended['speed']}{C.RESET}\n"
        f"  {C.DIM}  Ideal para: {recommended['best_for']}{C.RESET}\n"
    )

    chosen = recommended
    if not ask_yes_no("Usar este modelo?", default=True):
        chosen = choose_model(recommended)
        print(f"\n  {C.GREEN}✔ Escolhido:{C.RESET} {C.CYAN}{chosen['label']}{C.RESET}")

    # ── 3. Gera .env ──────────────────────────────────────────────────
    step("3 / 4  Gerando .env...")
    extraction_targets = choose_extraction_targets()
    config = generate_env(chosen, gpu, extraction_targets=extraction_targets)
    print(f"\n  {C.DIM}{'─' * 44}{C.RESET}")
    for key, value in config.items():
        print(f"  {C.YELLOW}{key}{C.RESET}={C.WHITE}{value}{C.RESET}")
    print(f"  {C.DIM}{'─' * 44}{C.RESET}\n")
    write_env(config)
    patch_compose(gpu)
    success(f"Arquivo {C.WHITE}.env{C.RESET}{C.GREEN} criado com sucesso!")

    # Cria pastas de output com permissão do usuário atual
    # evita que o Docker crie como root
    for folder in ["docs", "status", "projects", "logs"]:
        Path(folder).mkdir(exist_ok=True)
    success("Pastas docs/ status/ projects/ logs/ garantidas com permissão correta")

    # ── 4. Finaliza ───────────────────────────────────────────────────
    step("4 / 4  Tudo pronto!")
    info(f"Coloque seus projetos em {C.WHITE}./projects/{C.RESET}")
    info(f"Documentação gerada em   {C.WHITE}./docs/<projeto>/{C.RESET}")
    info(f"Resumo geral em          {C.WHITE}./docs/<projeto>/_resumo.md{C.RESET}")

    print()
    if ask_yes_no("Rodar agora? (docker compose up --build)", default=True):
        print()
        info("Iniciando... (pressione Ctrl+C para parar)\n")
        try:
            subprocess.run(["docker", "compose", "up", "--build"], check=True)
        except subprocess.CalledProcessError:
            print()
            error("Docker retornou um erro. Tente:")
            info("  python3 setup.py --reset")
        except KeyboardInterrupt:
            print()
            warn("Interrompido.")
        except FileNotFoundError:
            error("Docker não encontrado: https://docs.docker.com/get-docker/")
    else:
        info(f"Quando quiser rodar: {C.WHITE}docker compose up --build{C.RESET}")

    print(f"\n{C.BOLD}{C.PURPLE}{'─' * 50}{C.RESET}\n")


if __name__ == "__main__":
    main()