# Manus CLI — v0.1 Design

## Objetivo

CLI em Python que consome a API REST oficial do Manus (`https://api.manus.ai/v2`)
para criar e acompanhar tarefas a partir do terminal, com chat contínuo,
anexo de arquivo único e anexo de diretório de projeto.

## Auth

- `manus login`: prompt interativo (input mascarado) pela API key, valida
  chamando `GET /v2/task.list?limit=1`, salva em
  `~/.config/manus/credentials.json` com `chmod 600`.
- Toda requisição usa header `x-manus-api-key: <key>`.
- A key nunca é impressa; se precisar exibir, mascarar (`sk-...XXXX`).

## Estado local

`~/.config/manus/state.json`: guarda `last_task_id` (atualizado a cada
`task.create`/`sendMessage` bem-sucedido) para suportar `--continue`.

## Comandos

| Comando | Ação |
|---|---|
| `manus login` | configura API key |
| `manus "prompt"` | `POST task.create` → poll `task.listMessages` até `status: stopped` → imprime última `assistant_message` |
| `manus` (sem args) | REPL: primeiro prompt = `task.create`; seguintes = `task.sendMessage` no mesmo `task_id`; cada turno faz polling e imprime a resposta antes do próximo prompt |
| `manus --continue "prompt"` | usa `last_task_id` do state e chama `task.sendMessage` |
| `manus --file <path> "prompt"` | `file.upload` (nome) → `PUT` bytes no `upload_url` → `task.create`/`sendMessage` com `{"type":"file","file_id":...}` no content |
| `manus --project <dir>` | varre `<dir>` recursivamente, pulando `.git/`, diretórios/arquivos ignorados por `.gitignore` (se existir) e binários grandes (>10MB ignorados com aviso); sobe cada arquivo via `file.upload`+PUT; cria task com todos os `file_id` anexados + prompt opcional |
| `manus status [task_id]` | `GET task.detail`; sem `task_id` usa `last_task_id`; imprime `status`, `credit_usage`, `task_url` |
| `manus result [task_id]` | `GET task.listMessages` (order=desc, limit=5); imprime a última `assistant_message.content` |

Flags globais: `--timeout <segundos>` (default 300) no polling.

## Polling

Loop: `GET task.listMessages?task_id=...&order=desc&limit=1` a cada 2s.
Para quando a mensagem mais nova tem `type: assistant_message` E
`task.detail.status == stopped`, ou quando estoura `--timeout`. Ctrl+C
interrompe só o polling local (a task continua rodando no Manus).

## Erros

Resposta HTTP com `"ok": false` → imprime `error.code: error.message`
formatado (`rich`) em stderr, `sys.exit(1)`. Timeout de rede/polling →
mensagem clara + exit code 1.

## Fora de escopo (v0.1)

Projects (agrupamento de tasks), webhooks, seleção de `agent_profile`
via flag, `task.confirmAction` (tasks que pausam esperando confirmação
do usuário — hoje só fica visível como status `waiting`), múltiplas
API keys.

## Estrutura de arquivos

```
manus_cli/
  __init__.py
  cli.py          # comandos click
  api.py          # cliente httpx (task.*, file.*)
  config.py       # credentials.json / state.json
  render.py       # formatação rich (mensagens, erro, status)
pyproject.toml
```

## Teste mínimo

`tests/test_api.py`: valida o parsing de `task.listMessages` (extrai
última `assistant_message`) e a montagem do payload de `--file`/
`--project`, usando respostas mockadas — sem chamada de rede real.
