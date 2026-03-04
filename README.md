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