"""
Documently — Extractor
Extrai funções/classes de arquivos de código usando tree-sitter.
Fallback para regex se a linguagem não tiver grammar instalada.

Instalação das grammars (adicionar ao Dockerfile):
  pip install tree-sitter==0.21.3 \
    tree-sitter-javascript \
    tree-sitter-typescript \
    tree-sitter-python \
    tree-sitter-java \
    tree-sitter-rust \
    tree-sitter-go
"""

import re
from pathlib import Path
from dataclasses import dataclass, field

from logger import log_warn, log_info


@dataclass
class FunctionNode:
    name: str
    body: str
    start_line: int
    end_line: int
    kind: str = "function"   # function | method | class


# ── Tree-sitter queries por linguagem ─────────────────────────────────
# Captura funções, métodos e classes com seus corpos completos

QUERIES = {
    "javascript": """
        (function_declaration name: (identifier) @name) @body
        (method_definition key: (property_identifier) @name) @body
        (variable_declarator
            name: (identifier) @name
            value: [(arrow_function) (function_expression)]) @body
        (class_declaration name: (identifier) @name) @body
    """,
    "typescript": """
        (function_declaration name: (identifier) @name) @body
        (method_definition key: (property_identifier) @name) @body
        (variable_declarator
            name: (identifier) @name
            value: [(arrow_function) (function_expression)]) @body
        (class_declaration name: (identifier) @name) @body
    """,
    "python": """
        (function_definition name: (identifier) @name) @body
        (class_definition name: (identifier) @name) @body
    """,
    "java": """
        (method_declaration name: (identifier) @name) @body
        (class_declaration name: (identifier) @name) @body
        (constructor_declaration name: (identifier) @name) @body
    """,
    "rust": """
        (function_item name: (identifier) @name) @body
        (impl_item) @body
        (struct_item name: (type_identifier) @name) @body
    """,
    "go": """
        (function_declaration name: (identifier) @name) @body
        (method_declaration name: (field_identifier) @name) @body
        (type_declaration) @body
    """,
}

# Mapa de lang_label → módulo tree-sitter
GRAMMAR_MODULES = {
    "javascript":  "tree_sitter_javascript",
    "typescript":  "tree_sitter_typescript",
    "python":      "tree_sitter_python",
    "java":        "tree_sitter_java",
    "rust":        "tree_sitter_rust",
    "go":          "tree_sitter_go",
}

# Fallback regex por linguagem quando tree-sitter não está disponível
FALLBACK_PATTERNS = {
    "javascript": re.compile(
        r"(?:^|\n)(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|"
        r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[\w]+)\s*=>|"
        r"(?:class)\s+(\w+))",
        re.MULTILINE,
    ),
    "typescript": re.compile(
        r"(?:^|\n)(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|"
        r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[\w]+)\s*=>|"
        r"(?:class)\s+(\w+))",
        re.MULTILINE,
    ),
    "python": re.compile(
        r"^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)",
        re.MULTILINE,
    ),
    "java": re.compile(
        r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(",
        re.MULTILINE,
    ),
    "rust": re.compile(
        r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)|(?:pub\s+)?struct\s+(\w+)|(?:pub\s+)?impl\s+(\w+)",
        re.MULTILINE,
    ),
    "go": re.compile(
        r"^func\s+(?:\([^)]+\)\s+)?(\w+)|^type\s+(\w+)",
        re.MULTILINE,
    ),
    "solidity": re.compile(
        r"function\s+(\w+)|contract\s+(\w+)|modifier\s+(\w+)|event\s+(\w+)",
        re.MULTILINE,
    ),
}


def _try_treesitter(content: str, lang_key: str) -> list[FunctionNode] | None:
    """Tenta extrair com tree-sitter. Retorna None se não disponível."""
    module_name = GRAMMAR_MODULES.get(lang_key)
    query_str   = QUERIES.get(lang_key)
    if not module_name or not query_str:
        return None

    try:
        import tree_sitter
        import importlib
        grammar_mod = importlib.import_module(module_name)

        # API tree-sitter >= 0.21
        lang     = tree_sitter.Language(grammar_mod.language())
        parser   = tree_sitter.Parser(lang)
        tree     = parser.parse(content.encode())
        query    = lang.query(query_str)
        captures = query.captures(tree.root_node)

        nodes: list[FunctionNode] = []
        seen: set[tuple] = set()

        # captures é dict[str, list[Node]] na API 0.21+
        bodies = captures.get("body", [])
        names  = {n.start_byte: n for n in captures.get("name", [])}

        for body_node in bodies:
            key = (body_node.start_byte, body_node.end_byte)
            if key in seen:
                continue
            seen.add(key)

            # Tenta achar o nome associado
            name_node = names.get(body_node.start_byte)
            name = name_node.text.decode() if name_node else "<anônimo>"
            body = content[body_node.start_byte:body_node.end_byte]

            nodes.append(FunctionNode(
                name       = name,
                body       = body,
                start_line = body_node.start_point[0] + 1,
                end_line   = body_node.end_point[0] + 1,
                kind       = "class" if "class" in body_node.type else "function",
            ))

        return nodes or None

    except ImportError:
        return None
    except Exception as e:
        log_warn(f"tree-sitter falhou ({lang_key}): {e} — usando fallback")
        return None


def _fallback_regex(content: str, lang_key: str) -> list[FunctionNode]:
    """Extrai apenas nomes de funções via regex, sem corpo."""
    pattern = FALLBACK_PATTERNS.get(lang_key)
    if not pattern:
        return []

    nodes = []
    lines = content.splitlines()
    for match in pattern.finditer(content):
        # Pega o primeiro grupo não-None como nome
        name = next((g for g in match.groups() if g), "<anônimo>")
        # Descobre a linha
        line_no = content[:match.start()].count("\n") + 1
        # Corpo: pega da linha do match até a próxima definição ou 50 linhas
        start = line_no - 1
        end   = min(start + 50, len(lines))
        body  = "\n".join(lines[start:end])
        nodes.append(FunctionNode(name=name, body=body, start_line=line_no, end_line=end))

    return nodes


def extract_functions(content: str, lang_key: str, filename: str) -> list[FunctionNode]:
    """
    Extrai funções/classes do arquivo usando tree-sitter (preciso)
    ou regex (fallback). Retorna lista vazia se nada for encontrado.
    """
    # Normaliza lang_key (profiles usam "javascript", "python", etc.)
    key = lang_key.lower().replace("/typescript", "").replace("javascript", "javascript")
    if "typescript" in filename.lower() or filename.endswith(".ts") or filename.endswith(".tsx"):
        key = "typescript"

    nodes = _try_treesitter(content, key)
    if nodes is not None:
        log_info(f"tree-sitter: {len(nodes)} nó(s) em {filename}")
        return nodes

    nodes = _fallback_regex(content, key)
    if nodes:
        log_warn(f"regex fallback: {len(nodes)} função(ões) em {filename} (instale tree-sitter para melhor resultado)")
    return nodes


def functions_to_scan_prompt(nodes: list[FunctionNode], filename: str) -> str:
    """Monta lista de assinaturas para o scan inicial (barato)."""
    if not nodes:
        return ""
    lines = [f"- `{n.name}` (linha {n.start_line}–{n.end_line}, {n.kind})" for n in nodes]
    return f"Funções/classes encontradas em `{filename}`:\n" + "\n".join(lines)