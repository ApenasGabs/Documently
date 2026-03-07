## ⚙️ Parâmetros de contexto e limites por etapa

O comportamento do analisador pode ser ajustado via variáveis no `.env`:

| Variável                | Descrição                                              | Default |
|-------------------------|--------------------------------------------------------|---------|
| OLLAMA_NUM_CTX          | Tamanho máximo do contexto (tokens) por chamada         | 4096    |
| SCAN_NUM_PREDICT        | Tokens para etapa de scan (assinaturas)                | 512     |
| DEEP_NUM_PREDICT        | Tokens para deep dive (função/classe)                  | 1024    |
| SYNTH_NUM_PREDICT       | Tokens para síntese do arquivo                         | 1024    |
| SUMMARY_NUM_PREDICT     | Tokens para resumo do projeto                          | 1500    |
| CONTEXT_WINDOW_SIZE     | Máximo de entradas na janela de contexto entre arquivos | 12      |
| CONTEXT_SNIPPET_CHARS   | Tamanho máximo do trecho de contexto injetado no prompt | 320     |
| RUNNING_CONTEXT_CHARS   | Limite do contexto acumulado durante análise do arquivo | 700     |
| MAX_SCAN_ITEMS          | Máximo de itens listados no passo de scan              | 24      |
| MAX_DEEP_BODY_CHARS     | Máximo de caracteres do corpo no deep dive             | 2200    |
| MAX_SYNTH_ITEMS         | Máximo de anotações usadas na síntese                  | 20      |
| MAX_FUNCTION_DOC_ITEMS  | Máximo de funções detalhadas na seção por função       | 12      |
| DEEP_MAX_WORDS          | Limite de palavras por função/classe no deep dive      | 80      |
| DEEP_CONTEXT_CHARS      | Limite de contexto enviado no deep dive                | 180     |
| DEEP_BODY_CHAR_CAP      | Limite efetivo de código enviado no deep dive          | 1400    |
| DEEP_MIN_PREDICT        | Piso de tokens de resposta no deep dive                | 128     |
| DEEP_FUNCTION_PREDICT   | Teto de tokens para funções no deep dive               | 220     |
| DEEP_CLASS_PREDICT      | Teto de tokens para classes no deep dive               | 320     |
| EXTRACT_TARGETS         | Escopo de extração funcional (csv de alvos)            | business_rules,validations,integrations,dependencies |
| SYNTH_MAX_WORDS         | Limite de palavras na síntese funcional do arquivo     | 260     |
| FALLBACK_MAX_WORDS      | Limite de palavras no fallback sem funções detectadas  | 180     |
| MAX_PROJECT_SUMMARY_ITEMS | Máximo de arquivos incluídos no resumo final         | 40      |
| TELEMETRY_ENABLED       | Habilita escrita de telemetria JSONL                    | 1       |
| TELEMETRY_LOG_DIR       | Diretório de logs persistentes dentro do container      | /output/logs |
| TELEMETRY_LOG_FILE      | Arquivo JSONL das chamadas Ollama                       | ollama_telemetry.jsonl |
| TRUNCATION_STATS_FILE   | Arquivo agregado por extensão/etapa                     | truncation_stats.json |
| PROMPT_DEBUG_LOG        | Mostra preview do prompt no stdout do container         | 1       |
| PROMPT_LOG_MAX_CHARS    | Tamanho máximo do preview do prompt                      | 1200    |
| PROMPT_LOG_INCLUDE_FULL | Loga prompt completo (cuidado com volume)               | 0       |

Esses valores podem ser ajustados no `.env` **sem editar o código**. O setup já gera os defaults recomendados.

Os artefatos de telemetria ficam em `./logs/` no host (montado do container):
- `ollama_telemetry.jsonl`: uma linha JSON por chamada/retry/erro
- `truncation_stats.json`: agregados por extensão e etapa para tuning

Se `./logs/` não estiver gravável, o analyzer usa fallback em `/tmp/documently-logs` dentro do container e registra aviso no stdout.

Por padrão, a documentação agora prioriza entendimento funcional/regra de negócio e evita descrição linha a linha.
Prompts e documentação gerada agora usam inglês por padrão para reduzir consumo de tokens.

# 🔍 Documently - Local Code Analyzer

Analisa e documenta repositórios de código usando IA local (Ollama) via Docker, sem enviar nada para a nuvem.

## 📁 Estrutura

```
documently/
├── docker-compose.yml        ← orquestração dos serviços (Ollama + analyzer)
├── setup.py                  ← configuração interativa (rode isso primeiro!)
├── .env                      ← gerado pelo setup.py (não suba no git)
│
├── analyzer/                 ← código do analisador
│   ├── main.py               ← loop principal: varre projetos, chama Ollama, salva docs
│   └── profiles.py           ← perfis por linguagem: triggers, extensões, prompts, ignore_dirs
│
├── projects/                 ← coloque seus projetos aqui (qualquer nome)
│   ├── meu-contrato/
│   └── outro-repo/
│
├── docs/                     ← documentação gerada automaticamente (auto-criada)
│   ├── meu-contrato/
│   │   ├── _resumo.md        ← visão geral do projeto gerada no final
│   │   ├── src/
│   │   │   └── index.md      ← espelha a estrutura do projeto
│   │   └── contracts/
│   │       └── Token.md
│   └── outro-repo/
│       └── _resumo.md
│
└── status/                   ← progresso de cada projeto (auto-criado)
    ├── meu-contrato.json
    └── outro-repo.json
```

## 🚀 Como usar

### 1. Pré-requisitos

```bash
# Linux — Docker + Docker Compose
sudo apt install docker.io docker-compose-plugin -y
sudo usermod -aG docker $USER
# (logout e login para aplicar o grupo)

# Opcional: suporte a GPU Nvidia
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html
```

> **Windows/Mac:** instale o [Docker Desktop](https://docs.docker.com/get-docker/)

### 2. Configure o ambiente

```bash
# Linux/Mac
python3 setup.py

# Windows
python setup.py
```

O setup detecta seu hardware automaticamente, recomenda o melhor modelo e gera o `.env`:

```
1 / 4  Detectando hardware...
  ✅ RAM detectada: 16 GB
  ✅ GPU detectada: NVIDIA RTX 3060 com 6.0 GB de VRAM

2 / 4  Recomendando modelo...
  ▶ DeepSeek Coder V2 16B
    Tamanho: ~8.9GB | Qualidade: excelente | Velocidade: média

  Usar este modelo? (S/n):
```

### 3. Clone seus projetos dentro de `projects/`

```bash
git clone https://github.com/user/meu-contrato  ./projects/meu-contrato
git clone https://github.com/user/outro-repo    ./projects/outro-repo
```

> Sem restrição de nomes ou quantidade — qualquer pasta dentro de `projects/` é analisada automaticamente.

### 4. Rode

```bash
# Primeira vez (baixa o modelo, pode demorar)
docker compose up

# Próximas vezes (retoma de onde parou)
docker compose up analyzer

# Rodar em background
docker compose up -d && docker compose logs -f analyzer
```

### 5. Acompanhe o progresso

```bash
# Status em tempo real
watch -n 2 cat status/meu-contrato.json

# Ver resumo sendo gerado
tail -f docs/meu-contrato/_resumo.md
```

## 🔎 Detecção automática de linguagem

O analisador identifica o tipo de projeto pelos arquivos de configuração e aplica prompts especializados:

| Arquivo detectado | Perfil | Foco da análise |
|---|---|---|
| `hardhat.config.*`, `foundry.toml` | Solidity | Auditoria: funções, eventos, riscos de segurança |
| `package.json` | JavaScript/TypeScript | Exports, tipos, efeitos colaterais |
| `pom.xml`, `build.gradle` | Java | Classes, métodos públicos, anotações |
| `requirements.txt`, `pyproject.toml` | Python | Funções, dependências, fluxo principal |
| `Cargo.toml` | Rust | Structs, traits, uso de unsafe |
| `go.mod` | Go | Pacotes, goroutines, tratamento de erros |
| _(nenhum)_ | Fallback | Análise geral com extensões do `.env` |

## 🤖 Modelos recomendados por hardware

| Modelo | Tamanho | VRAM mínima | RAM mínima | Qualidade | Velocidade |
|---|---|---|---|---|---|
| `qwen2.5-coder:3b` | ~1.9GB | CPU ok | 8GB | boa | rápida |
| `qwen2.5-coder:7b` | ~4.7GB | 4GB | 12GB | ótima | média |
| `deepseek-coder-v2:16b` | ~8.9GB | 8GB | 16GB | excelente | média |
| `qwen2.5-coder:14b` | ~9.0GB | 8GB | 16GB | excelente | lenta |
| `qwen2.5-coder:32b` | ~19GB | 20GB | 32GB | state-of-the-art | lenta |

> O `setup.py` detecta seu hardware e escolhe automaticamente o melhor modelo disponível.  
> Para reconfigurar: `python3 setup.py`

## 📄 Documentação gerada

Cada projeto gera uma pasta espelhando sua estrutura:

```
docs/querocasa/
├── _resumo.md              ← resumo executivo gerado no final
├── api/
│   ├── server.md
│   └── routes/
│       └── geolocations.md
└── src/
    ├── App.md
    └── components/
        └── Map.md
```

O `_resumo.md` consolida toda a análise em um relatório executivo com:
- Visão geral do projeto
- Arquitetura e padrões identificados
- Arquivos agrupados por responsabilidade
- Dependências principais
- Pontos de atenção e riscos

## 🔄 Retomada automática

Se o processo for interrompido, basta rodar `docker compose up analyzer` novamente.
O progresso é salvo em `status/<projeto>.json` por arquivo e chunk — o loop retoma exatamente de onde parou.

Para forçar re-análise completa de um projeto:
```bash
rm -rf status/meu-contrato.json docs/meu-contrato/
```

## 📊 Formato do arquivo de status

```json
{
  "project": "meu-contrato",
  "profile": "Solidity",
  "started_at": "2024-01-15T10:30:00",
  "finished_at": "2024-01-15T10:42:00",
  "files": {
    "/projects/meu-contrato/contracts/Token.sol": {
      "chunks_done": 2,
      "total_chunks": 2,
      "done": true,
      "doc_path": "/output/docs/meu-contrato/contracts/Token.md"
    }
  }
}
```

## 🔁 Responsabilidade de cada arquivo

| Arquivo | Quando mexer |
|---|---|
| `setup.py` | Ao configurar pela primeira vez ou trocar hardware |
| `docker-compose.yml` | Raramente — portas ou recursos avançados |
| `analyzer/profiles.py` | Adicionar linguagem nova ou ajustar prompts |
| `analyzer/main.py` | Raramente — só se mudar o fluxo do loop |
| `projects/*` | Sempre — aqui ficam os repos a analisar |
| `docs/*` | Nunca — gerado automaticamente |
| `status/*` | Nunca — gerado automaticamente (delete para re-analisar) |

## 💡 Sem GPU?

O setup detecta isso automaticamente e configura o Ollama para rodar via CPU.  
Funciona com 8GB+ de RAM usando o modelo `qwen2.5-coder:3b`, apenas mais lento.

## 🚦 Detecção de Framework (Feature 6)

O Documently agora detecta automaticamente o framework principal do projeto de forma híbrida:
- **Determinística:** por arquivos e dependências (ex: React, Gradle, Maven, Android)
- **Fallback IA:** se não for possível determinar, usa IA local para sugerir o framework

O framework detectado aparece no resumo do projeto e no status JSON.

Exemplo de frameworks suportados: React, Vite, Angular, Maven, Gradle, Android, etc.

Se não for possível identificar, retorna `unknown`.