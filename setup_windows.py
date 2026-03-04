"""
Documently — Setup Windows Nativo (sem Docker)
Instala Ollama.exe + dependências Python e roda o analyzer direto.

Requisitos:
  - Python 3.11+ (https://python.org)
  - Git (https://git-scm.com)
  - Windows 10/11

Uso:
  python setup_windows.py           # setup normal
  python setup_windows.py --reset   # limpa tudo e reconfigura
  python setup_windows.py --run     # só roda o analyzer (sem reconfigurar)
"""

import os
import sys
import json
import shutil
import platform
import subprocess
import urllib.request
from pathlib import Path

# ── Garante Windows ───────────────────────────────────────────────────
if platform.system() != "Windows":
    print("Este script é apenas para Windows.")
    print("No Linux/Mac, use: python3 setup.py")
    sys.exit(1)

# ── Cores ANSI (Windows 10+) ──────────────────────────────────────────
try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
    USE_COLOR = True
except Exception:
    USE_COLOR = False

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

def success(msg):   print(f"{C.GREEN}  [OK] {msg}{C.RESET}")
def warn(msg):      print(f"{C.YELLOW}  [!]  {msg}{C.RESET}")
def error(msg):     print(f"{C.RED}  [X]  {msg}{C.RESET}")
def info(msg):      print(f"{C.CYAN}  [i]  {msg}{C.RESET}")
def step(msg):      print(f"\n{C.BOLD}{C.WHITE}{msg}{C.RESET}")
def highlight(msg): print(f"{C.PURPLE}{C.BOLD}{msg}{C.RESET}")

def ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{hint}: ").strip()
        return answer if answer else default
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)

def ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint   = "S/n" if default else "s/N"
    answer = ask(f"{prompt} ({hint})", "s" if default else "n")
    return answer.lower() in ("s", "sim", "y", "yes", "")


# ── Paths ─────────────────────────────────────────────────────────────
ROOT_DIR      = Path(__file__).parent
ANALYZER_DIR  = ROOT_DIR / "analyzer"
PROJECTS_DIR  = ROOT_DIR / "projects"
DOCS_DIR      = ROOT_DIR / "docs"
STATUS_DIR    = ROOT_DIR / "status"
ENV_FILE      = ROOT_DIR / ".env.windows"
VENV_DIR      = ROOT_DIR / ".venv-documently"
OLLAMA_EXE    = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
OLLAMA_URL    = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_SETUP  = ROOT_DIR / "OllamaSetup.exe"

# Modelos disponíveis (mesmos do setup.py)
MODELS = [
    {"id": "qwen2.5-coder:3b",      "label": "Qwen 2.5 Coder 3B",     "size_gb": 1.9,  "min_vram": 0,  "min_ram": 8,  "quality": "boa",             "speed": "rápida", "best_for": "CPU ok, máquinas modestas"},
    {"id": "qwen2.5-coder:7b",      "label": "Qwen 2.5 Coder 7B",     "size_gb": 4.7,  "min_vram": 4,  "min_ram": 12, "quality": "ótima",           "speed": "média",  "best_for": "melhor custo-benefício"},
    {"id": "deepseek-coder-v2:16b", "label": "DeepSeek Coder V2 16B", "size_gb": 8.9,  "min_vram": 8,  "min_ram": 16, "quality": "excelente",       "speed": "média",  "best_for": "análise detalhada"},
    {"id": "qwen2.5-coder:14b",     "label": "Qwen 2.5 Coder 14B",    "size_gb": 9.0,  "min_vram": 8,  "min_ram": 16, "quality": "excelente",       "speed": "lenta",  "best_for": "projetos grandes"},
    {"id": "qwen2.5-coder:32b",     "label": "Qwen 2.5 Coder 32B",    "size_gb": 19.0, "min_vram": 20, "min_ram": 32, "quality": "state-of-the-art","speed": "lenta",  "best_for": "máxima qualidade"},
]


# ── Hardware ──────────────────────────────────────────────────────────

def get_ram_gb() -> int:
    try:
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
    """Detecta GPU Nvidia ou AMD no Windows via nvidia-smi ou WMI."""

    # ── Nvidia (nvidia-smi disponível) ────────────────────────────────
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

    # ── WMI (Nvidia ou AMD sem nvidia-smi) ───────────────────────────
    try:
        out = subprocess.check_output(
            ["wmic", "path", "win32_VideoController",
             "get", "Name,AdapterRAM", "/format:csv"],
            stderr=subprocess.DEVNULL
        ).decode(errors="ignore").strip()
        for line in out.splitlines():
            parts    = [p.strip() for p in line.split(",")]
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            name     = parts[2]
            vram_gb  = round(int(parts[1]) / 1024 / 1024 / 1024, 1)
            name_low = name.lower()
            if vram_gb < 0.5:
                continue
            if "nvidia" in name_low:
                return {"found": True, "vendor": "nvidia", "name": name, "vram_gb": vram_gb}
            elif "amd" in name_low or "radeon" in name_low:
                return {"found": True, "vendor": "amd", "name": name, "vram_gb": vram_gb}
    except Exception:
        pass

    return {"found": False, "vendor": None}


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


# ── Ollama ────────────────────────────────────────────────────────────

def is_ollama_installed() -> bool:
    return OLLAMA_EXE.exists() or shutil.which("ollama") is not None


def get_ollama_cmd() -> str:
    if shutil.which("ollama"):
        return "ollama"
    if OLLAMA_EXE.exists():
        return str(OLLAMA_EXE)
    return "ollama"


def install_ollama():
    if is_ollama_installed():
        success("Ollama já instalado")
        return

    step("Baixando Ollama para Windows...")
    info(f"URL: {OLLAMA_URL}")

    def progress(block, block_size, total):
        if total > 0:
            pct = block * block_size * 100 // total
            print(f"\r  Baixando... {pct}%", end="", flush=True)

    try:
        urllib.request.urlretrieve(OLLAMA_URL, OLLAMA_SETUP, reporthook=progress)
        print()
        success("Download concluído")
    except Exception as e:
        error(f"Falha no download: {e}")
        info(f"Baixe manualmente em: {OLLAMA_URL}")
        info(f"E instale antes de continuar.")
        sys.exit(1)

    info("Executando instalador do Ollama (siga as instruções na tela)...")
    try:
        subprocess.run([str(OLLAMA_SETUP), "/S"], check=True)  # /S = silent install
        success("Ollama instalado com sucesso")
        OLLAMA_SETUP.unlink(missing_ok=True)
    except Exception as e:
        warn(f"Instalação silenciosa falhou, abrindo instalador: {e}")
        os.startfile(str(OLLAMA_SETUP))
        input("  Pressione Enter após concluir a instalação...")


def start_ollama_server() -> subprocess.Popen | None:
    """Inicia o servidor Ollama em background se não estiver rodando."""
    import urllib.error
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        success("Ollama já está rodando")
        return None
    except Exception:
        pass

    info("Iniciando servidor Ollama em background...")
    ollama_cmd = get_ollama_cmd()
    proc = subprocess.Popen(
        [ollama_cmd, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Aguarda subir
    import time
    for i in range(15):
        time.sleep(1)
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
            success("Ollama iniciado")
            return proc
        except Exception:
            pass

    warn("Ollama demorou para responder — continuando assim mesmo")
    return proc


def pull_model(model_id: str):
    """Baixa o modelo se ainda não estiver disponível."""
    import urllib.error
    try:
        resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5)
        data = json.loads(resp.read())
        if any(model_id in m["name"] for m in data.get("models", [])):
            success(f"Modelo '{model_id}' já disponível")
            return
    except Exception:
        pass

    info(f"Baixando modelo {model_id} (pode demorar)...")
    ollama_cmd = get_ollama_cmd()
    try:
        subprocess.run([ollama_cmd, "pull", model_id], check=True)
        success(f"Modelo '{model_id}' baixado com sucesso")
    except subprocess.CalledProcessError as e:
        error(f"Falha ao baixar modelo: {e}")
        sys.exit(1)


# ── Python venv + dependências ────────────────────────────────────────

def setup_venv():
    """Cria venv isolado para o Documently não poluir o Python do sistema."""
    if VENV_DIR.exists():
        success("venv já existe")
        return

    info("Criando ambiente virtual Python...")
    subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    success("venv criado")


def get_venv_python() -> str:
    python = VENV_DIR / "Scripts" / "python.exe"
    if python.exists():
        return str(python)
    return sys.executable


def install_dependencies():
    python = get_venv_python()
    deps   = [
        "requests",
        "tree-sitter==0.21.3",
        "tree-sitter-javascript",
        "tree-sitter-typescript",
        "tree-sitter-python",
        "tree-sitter-java",
        "tree-sitter-rust",
        "tree-sitter-go",
    ]
    info("Instalando dependências Python...")
    try:
        subprocess.run(
            [python, "-m", "pip", "install", "--quiet", "--upgrade"] + deps,
            check=True
        )
        success("Dependências instaladas")
    except subprocess.CalledProcessError as e:
        error(f"Falha ao instalar dependências: {e}")
        sys.exit(1)


# ── .env.windows ──────────────────────────────────────────────────────

def write_windows_env(model_id: str, gpu: dict):
    gpu_layers = 0
    vendor     = gpu.get("vendor") if gpu.get("found") else None

    if gpu["found"]:
        gpu_layers = min(35, int((gpu["vram_gb"] * 1024) / 200))

    # No Windows o Ollama usa DirectML para AMD (sem precisar do ROCm)
    # e CUDA para Nvidia — ambos configurados automaticamente pelo Ollama.exe.
    # Só precisamos garantir OLLAMA_NUM_GPU_LAYERS > 0 para ativar a GPU.
    config = {
        "OLLAMA_MODEL":             model_id,
        "OLLAMA_HOST":              "http://localhost:11434",
        "MAX_TOKENS_PER_CHUNK":     "3000",
        "EXTENSIONS":               ".sol,.py,.js,.ts,.go,.rs,.java",
        "OLLAMA_NUM_GPU_LAYERS":    str(gpu_layers),
        "OLLAMA_NUM_PARALLEL":      "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "GPU_VENDOR":               vendor or "cpu",
    }

    gpu_label = (
        f"DirectML/AMD ({gpu['name']})" if vendor == "amd"
        else f"CUDA/Nvidia ({gpu['name']})" if vendor == "nvidia"
        else "CPU only"
    )
    lines = [
        "# Documently — Windows Nativo",
        f"# GPU: {gpu_label}",
        "",
    ]
    for k, v in config.items():
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config
def load_windows_env() -> dict:
    if not ENV_FILE.exists():
        return {}
    config = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            config[k.strip()] = v.strip()
    return config


# ── Rodar o analyzer ──────────────────────────────────────────────────

def run_analyzer(ollama_proc=None):
    """Roda o main.py do analyzer com as variáveis do .env.windows."""
    config = load_windows_env()
    if not config:
        error("Configuração não encontrada. Rode setup_windows.py primeiro.")
        sys.exit(1)

    python = get_venv_python()
    env    = {**os.environ, **config}

    # Paths de output para Windows (usa pasta local em vez de /output)
    env["DOCS_DIR"]   = str(DOCS_DIR)
    env["STATUS_DIR"] = str(STATUS_DIR)

    DOCS_DIR.mkdir(exist_ok=True)
    STATUS_DIR.mkdir(exist_ok=True)
    PROJECTS_DIR.mkdir(exist_ok=True)

    info(f"Modelo: {config.get('OLLAMA_MODEL', '?')}")
    info(f"Projetos: {PROJECTS_DIR}")
    info(f"Docs: {DOCS_DIR}")
    print()

    try:
        subprocess.run(
            [python, str(ANALYZER_DIR / "main.py")],
            env=env,
            cwd=str(ANALYZER_DIR),
            check=True,
        )
    except subprocess.CalledProcessError as e:
        error(f"Analyzer falhou: {e}")
    except KeyboardInterrupt:
        print()
        warn("Interrompido pelo usuário.")
        if ollama_proc:
            warn("Parando servidor Ollama...")
            ollama_proc.terminate()


# ── Reset ─────────────────────────────────────────────────────────────

def do_reset():
    warn("Isso vai apagar o venv, .env.windows, docs e status.")
    if not ask_yes_no("Confirmar reset?", default=False):
        info("Reset cancelado.")
        return

    for path in [VENV_DIR, ENV_FILE]:
        if Path(path).exists():
            if Path(path).is_dir():
                shutil.rmtree(path)
            else:
                Path(path).unlink()
            success(f"removido: {path}")

    if ask_yes_no("Limpar docs e status gerados também?", default=True):
        for folder in [DOCS_DIR, STATUS_DIR]:
            if folder.exists():
                shutil.rmtree(folder)
                success(f"removido: {folder}")

    success("Reset concluído — rode setup_windows.py para reconfigurar")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    os.system("cls")
    print(f"\n{C.BOLD}{C.PURPLE}{'─' * 50}{C.RESET}")
    highlight("   Documently — Setup Windows Nativo")
    print(f"{C.BOLD}{C.PURPLE}{'─' * 50}{C.RESET}\n")
    info("Roda direto no Windows sem Docker nem WSL.")
    info("Economiza 2-4GB de RAM em relação ao Docker.\n")

    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    if "--reset" in args:
        do_reset()
        sys.exit(0)

    if "--run" in args:
        # Só roda — sem reconfigurar
        ollama_proc = start_ollama_server()
        config      = load_windows_env()
        if config:
            pull_model(config.get("OLLAMA_MODEL", "qwen2.5-coder:3b"))
        run_analyzer(ollama_proc)
        sys.exit(0)

    # ── 1. Hardware ───────────────────────────────────────────────────
    step("1 / 5  Detectando hardware...")
    ram_gb = get_ram_gb()
    gpu    = get_gpu_info()

    if ram_gb:
        success(f"RAM: {ram_gb} GB")
    else:
        warn("RAM não detectada")
        ram_gb = int(ask("RAM total em GB", "16"))

    if gpu["found"]:
        vendor = gpu.get("vendor", "")
        if vendor == "nvidia":
            success(f"GPU Nvidia: {gpu['name']} ({gpu['vram_gb']} GB VRAM) — CUDA")
        elif vendor == "amd":
            success(f"GPU AMD: {gpu['name']} ({gpu['vram_gb']} GB VRAM) — DirectML")
            info("AMD no Windows usa DirectML (nativo no Ollama.exe, sem instalar ROCm)")
        else:
            success(f"GPU: {gpu['name']} ({gpu['vram_gb']} GB VRAM)")
    else:
        warn("GPU dedicada não detectada — usará CPU")
        if ask_yes_no("Você tem GPU (Nvidia ou AMD) não detectada?", default=False):
            vram   = float(ask("GB de VRAM?", "4"))
            name   = ask("Nome da GPU (ex: RTX 3060 / RX 6700 XT)", "GPU")
            vendor = "amd" if any(x in name.lower() for x in ["amd", "rx", "radeon"]) else "nvidia"
            gpu    = {"found": True, "vendor": vendor, "name": name, "vram_gb": vram}

    # ── 2. Ollama ─────────────────────────────────────────────────────
    step("2 / 5  Verificando Ollama...")
    install_ollama()

    # ── 3. Modelo ─────────────────────────────────────────────────────
    step("3 / 5  Escolhendo modelo...")
    recommended = recommend_model(ram_gb, gpu)
    print(f"\n  Recomendado: {C.CYAN}{recommended['label']}{C.RESET}")
    print(f"  Tamanho: ~{recommended['size_gb']}GB | Qualidade: {recommended['quality']}\n")

    chosen = recommended
    if not ask_yes_no("Usar este modelo?", default=True):
        print()
        for i, m in enumerate(MODELS):
            marker = ">" if m["id"] == recommended["id"] else " "
            print(f"  {marker} [{i+1}] {m['label']} (~{m['size_gb']}GB) — {m['quality']}")
        print()
        idx = ask(f"Escolha [1-{len(MODELS)}]", str(MODELS.index(recommended) + 1))
        try:
            chosen = MODELS[int(idx) - 1]
        except (ValueError, IndexError):
            chosen = recommended
        success(f"Escolhido: {chosen['label']}")

    # ── 4. Python + dependências ──────────────────────────────────────
    step("4 / 5  Configurando Python...")
    setup_venv()
    install_dependencies()
    config = write_windows_env(chosen["id"], gpu)
    success(".env.windows criado")

    print(f"\n  {C.DIM}{'─' * 44}{C.RESET}")
    for k, v in config.items():
        print(f"  {C.YELLOW}{k}{C.RESET}={C.WHITE}{v}{C.RESET}")
    print(f"  {C.DIM}{'─' * 44}{C.RESET}")

    # ── 5. Inicia ─────────────────────────────────────────────────────
    step("5 / 5  Tudo pronto!")
    info(f"Projetos em: {PROJECTS_DIR}")
    info(f"Docs em:     {DOCS_DIR}")
    print()

    if ask_yes_no("Rodar agora?", default=True):
        ollama_proc = start_ollama_server()
        pull_model(chosen["id"])
        run_analyzer(ollama_proc)
    else:
        info("Para rodar depois:")
        info("  python setup_windows.py --run")

    print(f"\n{C.BOLD}{C.PURPLE}{'─' * 50}{C.RESET}\n")


if __name__ == "__main__":
    main()