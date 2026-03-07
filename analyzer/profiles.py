"""
Documently — Perfis de linguagem
Cada perfil define:
  - triggers:     arquivos que identificam o tipo de projeto
  - extensions:   extensões de arquivo a analisar
  - ignore_dirs:  pastas a ignorar (build, deps, cache)
  - lang_label:   nome da linguagem para o prompt
  - prompt_focus: instruções específicas para o modelo

Para adicionar uma nova linguagem, basta copiar um bloco
e ajustar os campos acima.
"""

import os
from pathlib import Path

PROFILES = {
    "solidity": {
        "triggers": [
            "hardhat.config.js",
            "hardhat.config.ts",
            "truffle-config.js",
            "foundry.toml",
            "brownie-config.yaml",
        ],
        "extensions": [".sol"],
        "ignore_dirs": {"artifacts", "cache", "out", "node_modules", ".git"},
        "lang_key": "javascript",
    "lang_label": "Solidity",
        "prompt_focus": (
            "Focus on business behavior and security:\n"
            "- Public/external functions and access control\n"
            "- Events and when they are emitted\n"
            "- Security risks (reentrancy, overflow, access control)\n"
            "- Standards/patterns used (Ownable, Pausable, ERC20, ERC721)"
        ),
    },
    "javascript": {
        "triggers": ["package.json"],
        "extensions": [".js", ".ts", ".jsx", ".tsx"],
        "ignore_dirs": {"node_modules", "dist", ".next", "build", "coverage", ".git"},
        "lang_key": "javascript",
    "lang_label": "JavaScript/TypeScript",
        "prompt_focus": (
            "Focus on business behavior:\n"
            "- Exported functions/components\n"
            "- Relevant types/interfaces\n"
            "- Side effects, API calls, global state usage\n"
            "- External dependencies"
        ),
    },
    "java": {
        "triggers": [
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        ],
        "extensions": [".java"],
        "ignore_dirs": {"target", "build", "bin", ".gradle", ".mvn", ".git"},
        "lang_key": "java",
    "lang_label": "Java",
        "prompt_focus": (
            "Focus on business behavior:\n"
            "- Classes/interfaces and responsibilities\n"
            "- Public methods and contracts\n"
            "- Relevant annotations (Spring, JPA, Lombok)\n"
            "- Design patterns when meaningful"
        ),
    },
    "python": {
        "triggers": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"],
        "extensions": [".py"],
        "ignore_dirs": {"__pycache__", ".venv", "venv", "dist", ".egg-info", ".git"},
        "lang_key": "python",
    "lang_label": "Python",
        "prompt_focus": (
            "Focus on business behavior:\n"
            "- Main functions/classes\n"
            "- Input/output contracts\n"
            "- External dependencies\n"
            "- Main execution flow"
        ),
    },
    "rust": {
        "triggers": ["Cargo.toml"],
        "extensions": [".rs"],
        "ignore_dirs": {"target", ".git"},
        "lang_key": "rust",
    "lang_label": "Rust",
        "prompt_focus": (
            "Focus on business behavior:\n"
            "- Structs/enums/traits\n"
            "- Public functions and contracts\n"
            "- Unsafe usage and rationale\n"
            "- Relevant ownership/lifetime constraints"
        ),
    },
    "go": {
        "triggers": ["go.mod"],
        "extensions": [".go"],
        "ignore_dirs": {"vendor", ".git"},
        "lang_key": "go",
    "lang_label": "Go",
        "prompt_focus": (
            "Focus on business behavior:\n"
            "- Packages and exported functions\n"
            "- Structs/interfaces\n"
            "- Goroutines/channel usage\n"
            "- Error handling strategy"
        ),
    },
    "fallback": {
        "triggers": [],
        "extensions": list(set(
            os.getenv("EXTENSIONS", ".sol,.py,.js,.ts,.go,.rs,.java").split(",")
        )),
        "ignore_dirs": {".git", "node_modules", "target", "build", "dist", "__pycache__"},
        "lang_label": "código",
        "prompt_focus": (
            "Analyze and document:\n"
            "- Functional purpose\n"
            "- Main functions/structures\n"
            "- Risks or issues"
        ),
    },
}


def detect_profile(project_path: Path) -> dict:
    """Detecta o perfil do projeto pelos arquivos de configuração presentes na raiz."""
    files_in_root = {f.name for f in project_path.iterdir() if f.is_file()}

    for profile_name, profile in PROFILES.items():
        if profile_name == "fallback":
            continue
        for trigger in profile["triggers"]:
            if trigger in files_in_root:
                print(f"[profiles] perfil detectado: {profile_name} (trigger: {trigger})", flush=True)
                return profile

    print("[profiles] nenhum perfil detectado, usando fallback", flush=True)
    return PROFILES["fallback"]