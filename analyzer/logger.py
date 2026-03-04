"""
Documently — Logger
Funções de log com timestamp e contexto de projeto/arquivo.
"""

from datetime import datetime


def log(level: str, msg: str, project: str = "", file: str = ""):
    ts = datetime.now().strftime("%H:%M:%S")
    parts = [f"[{ts}]", f"{level:<5}"]
    if project:
        parts.append(f"[{project}]")
    if file:
        parts.append(f"[{file}]")
    parts.append(msg)
    print(" | ".join(parts), flush=True)

def log_info(msg, project="", file=""):  log("INFO ", msg, project, file)
def log_ok(msg, project="", file=""):    log("OK   ", msg, project, file)
def log_warn(msg, project="", file=""):  log("WARN ", msg, project, file)
def log_err(msg, project="", file=""):   log("ERROR", msg, project, file)
def log_skip(msg, project="", file=""):  log("SKIP ", msg, project, file)