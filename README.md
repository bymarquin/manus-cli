# manus-cli

CLI não-oficial para a [API do Manus](https://open.manus.ai/docs/v2/introduction) (v2). Cria e acompanha tarefas do Manus direto do terminal, com modo chat contínuo.

```
$ manus

  Manus CLI
  Projeto: ~/projetos/sistema-escola

Ctrl+C ou linha vazia para sair.

> pesquise como integrar X

⠋ Manus trabalhando...
✓ Tarefa concluída

resposta do agente aqui...
```

## Requisitos

- Python 3.9 ou superior
- Uma API key do Manus: manus.im → Settings → Integrations → Create API Key

## Instalação

### Opção 1 — pipx (recomendado, instala o comando `manus` globalmente)

**Linux / macOS:**
```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
# reabra o terminal, depois:
git clone https://github.com/bymarquin/manus-cli.git
cd manus-cli
pipx install .
```

**Windows (PowerShell):**
```powershell
py -m pip install --user pipx
py -m pipx ensurepath
# reabra o terminal, depois:
git clone https://github.com/bymarquin/manus-cli.git
cd manus-cli
pipx install .
```

### Opção 2 — venv (sem instalar globalmente)

```bash
git clone https://github.com/bymarquin/manus-cli.git
cd manus-cli
python3 -m venv .venv
```

Linux/macOS: `.venv/bin/pip install -e .` e rode com `.venv/bin/manus`
Windows: `.venv\Scripts\pip install -e .` e rode com `.venv\Scripts\manus`

## Uso

```bash
manus login                          # configura a API key (fica salva localmente)

manus "pesquise X e resuma"          # cria uma tarefa e espera o resultado
manus                                # modo chat contínuo (tela de conversa)
manus --continue "e sobre Y?"        # continua a última tarefa
manus --file relatorio.pdf "resuma"  # anexa um arquivo
manus --project .                    # sobe os arquivos do diretório atual como contexto

manus use <task_id>                  # fixa uma tarefa existente (ex: criada pela UI web) como a atual
manus use <task_id> --as backend     # idem, e salva um apelido pra ela
manus alias list                     # lista os apelidos salvos
manus --task backend "prompt"        # usa a tarefa do apelido "backend" nessa chamada
manus status [task_id]               # status da última tarefa (ou de um task_id específico)
manus result [task_id]               # última resposta do agente
manus history [limite]               # lista as tarefas recentes (padrão: 20)
manus open [task_id]                 # abre a tarefa no navegador
manus doctor                         # diagnóstico: versão, API key, conectividade, config

git diff | manus "revisa isso"       # lê stdin como parte do prompt
manus --json "prompt"                # saída em JSON (task_id/status/content/attachments), pra scripts
```

Variável de ambiente `MANUS_API_KEY` tem prioridade sobre a key salva em disco (útil em CI).

Flags globais:
- `--timeout <segundos>` (padrão 300) — tempo máximo esperando a tarefa terminar
- `--connector <nome>` (repetível) — habilita um connector já configurado na sua conta Manus (ex: `--connector github`) pra essa mensagem

Qualquer arquivo que o Manus anexar na resposta (código, planilha, imagem etc.) é baixado automaticamente pra `./manus-output/<task_id>/`.

## Autocomplete de shell

**Bash** (adicione ao `~/.bashrc`):
```bash
source /caminho/pra/manus-cli/completions/manus.bash
```

**Zsh** (adicione ao `~/.zshrc`, antes do `compinit` se possível):
```zsh
fpath+=(/caminho/pra/manus-cli/completions)
autoload -U compinit && compinit
```

## `.manusrc` por projeto

Crie um `.manusrc` (JSON) na raiz do repo pra fixar a tarefa/connectors padrão desse projeto — sem precisar rodar `manus use` toda vez que entrar na pasta:

```json
{
  "task_id": "hpxhrG09FTWCzJ2mYeSyF6",
  "connectors": ["github"]
}
```

Prioridade: `--continue` explícito > `.manusrc` > tarefa nova.

## Onde fica salvo

- `~/.config/manus/credentials.json` — sua API key (permissão `600`, só o seu usuário lê)
- `~/.config/manus/state.json` — id da última tarefa (usado por `--continue`, `status`, `result`)

No Windows esses arquivos ficam em `%USERPROFILE%\.config\manus\` (mesmo comportamento, via `pathlib.Path.home()`).

## Estrutura

```
manus_cli/
  cli.py       # comandos e parsing de argumentos
  api.py       # cliente HTTP da API do Manus (task.*, file.*)
  config.py    # credenciais e estado local
  render.py    # saída formatada no terminal (rich)
tests/
  test_api.py  # teste do parsing de mensagens (sem chamada de rede)
docs/superpowers/specs/
  *.md         # spec de design do projeto
```

## Rodando os testes

```bash
python -m unittest discover -s tests -v
```

13 testes, todos com rede mockada (nada bate na API real): parsing de mensagens, retry/backoff (sucesso após falha transitória, 5xx recuperável, 4xx sem retry, esgotamento de tentativas), filtro de segredos no `--project`, limite de tamanho, e proteção contra path traversal nos anexos.

Roda automaticamente no GitHub Actions a cada push/PR, em Linux/macOS/Windows, Python 3.9 e 3.12.

## Problema conhecido (aberto com o suporte do Manus) — e o workaround

No momento, `task.create` responde com sucesso (`task_id`/`task_url` válidos) mas a tarefa não é persistida do lado do Manus — `task.detail`/`task.listMessages`/`task.list` retornam `not_found` logo em seguida. Reportado para `api-support@manus.ai`. **Isolado**: o bug é só na criação via API. Tarefas criadas pela interface web do Manus funcionam perfeitamente via API (`task.detail`, `task.sendMessage`, `task.listMessages` — testado e confirmado).

**Workaround**: crie uma tarefa qualquer pela interface web (manus.im), pegue o `task_id` da URL (`https://manus.im/app/<task_id>`), e:

```bash
manus use <task_id>          # fixa essa tarefa como a atual
manus --continue "prompt"    # conversa nela via API normalmente
```

Repita `manus use <task_id>` sempre que quiser trocar de tarefa/contexto.

## Fora de escopo (v0.1)

Manus Projects (agrupamento de tarefas), webhooks, seleção de `agent_profile`, `task.confirmAction` (fica visível como status `waiting`, sem fluxo de confirmação ainda).
