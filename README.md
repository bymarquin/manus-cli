# manus-cli

CLI não-oficial para a [API do Manus](https://open.manus.ai/docs/v2/introduction) (v2). Cria e acompanha tarefas do Manus direto do terminal, com modo chat contínuo.

```
$ manus

  ❯ Manus CLI
    ~/projetos/sistema-escola

  /help para comandos · Ctrl+C ou linha vazia para sair

❯ pesquise como integrar X

⠋ verificando fontes...
✓ Tarefa stopped

resposta do agente aqui...
```

## Requisitos

- Uma API key do Manus: manus.im → Settings → Integrations → Create API Key
- Para instalar via **pipx**: Python 3.9 ou superior
- Para instalar via **npm**: Node.js 18+ (não precisa de Python — veja abaixo)

## Instalação

O núcleo do `manus-cli` é sempre o mesmo código Python (`manus_cli/`), independente de como você instala. `pipx` roda esse código com o Python do seu sistema; `npm` baixa um binário standalone (compilado com PyInstaller) que embute o próprio interpretador, então **não exige Python instalado**. Escolha a que for mais conveniente — o comportamento do comando `manus` é idêntico nas duas.

### Opção 1 — pipx (recomendado se você já tem Python)

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

### Opção 2 — npm (recomendado se você já tem Node.js, sem precisar de Python)

```bash
npm install -g manus-cli
manus login
```

Ou sem instalar globalmente, via `npx`:

```bash
npx manus-cli login
npx manus-cli "pesquise X e resuma"
```

O pacote `manus-cli` do npm é um **launcher fino**: ele só detecta seu sistema operacional/arquitetura e executa o binário nativo correto, instalado automaticamente como `optionalDependency`. Nenhuma lógica de negócio vive no lado Node — é 100% o mesmo `manus_cli` Python, compilado.

**Plataformas suportadas via npm:**

| Plataforma | Pacote nativo |
|---|---|
| macOS (Apple Silicon) | `manus-cli-darwin-arm64` |
| macOS (Intel) | `manus-cli-darwin-x64` |
| Linux x64 | `manus-cli-linux-x64` |
| Linux arm64 | `manus-cli-linux-arm64` |
| Windows x64 | `manus-cli-win32-x64` |

Fora dessa lista (ex: Linux 32-bit, FreeBSD), o launcher falha com um erro claro explicando que a plataforma não tem binário — use a Opção 1 (pipx) nesse caso.

**Requisitos mínimos dos binários nativos:**
- **macOS**: Apple Silicon é compilado no runner `macos-14`; Intel, no `macos-15-intel`. A versão mínima do sistema ainda será confirmada pelo `LC_BUILD_VERSION` dos artefatos gerados no primeiro CI real; não presumimos que seja igual à versão do runner.
- **Linux**: binário compilado em `ubuntu-latest`/`ubuntu-24.04-arm` — vinculado à **glibc**. Não funciona em distros musl-only (ex: Alpine) sem glibc compat; nesse caso use pipx.
- **CI multiplataforma**: o workflow `npm-release.yml` está configurado para executar smoke tests em runners macOS/Linux/Windows a cada push/PR/tag (não é build cruzado). Antes do primeiro release, confirme uma execução verde nesses runners; isso ainda não substitui uso extensivo em produção em cada plataforma.

**Troubleshooting do npm:**
- `manus-cli: o pacote nativo "..." não foi instalado` — normalmente acontece se a instalação rodou com `--no-optional`/`--omit=optional`. Reinstale sem essas flags: `npm install -g manus-cli`.
- `manus-cli: plataforma não suportada` — não existe binário pra sua combinação de SO/arquitetura; use pipx.
- O launcher nunca baixa código em `postinstall` — os binários vêm só via `optionalDependencies` normais do npm, então `npm audit`/instalação offline de um cache/mirror funcionam como qualquer outro pacote.

### Opção 3 — venv (sem instalar globalmente, requer Python)

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
manus --project . --dry-run          # só mostra o que seria enviado (arquivos + pulados), sem enviar nada nem precisar de key
manus --project . --allow-secret     # sobe também arquivos que parecem segredo (.env etc.)
manus --project . --no-gitignore     # ignora o .gitignore do projeto ao selecionar arquivos

manus use <task_id>                  # fixa uma tarefa existente (ex: criada pela UI web) como a atual
manus use <task_id> --as backend     # idem, e salva um apelido pra ela
manus alias list                     # lista os apelidos salvos
manus --task backend "prompt"        # usa a tarefa do apelido "backend" nessa chamada

manus connector list                 # lista os connectors da sua conta (id, nome, tipo, categoria)
manus --connector github "prompt"    # resolve "github" pro UUID do connector com esse nome (ou passe o UUID direto)

manus confirm <event_id>             # confirma uma ação pendente (task.confirmAction)
manus confirm <event_id> --input '{"foo": "bar"}'  # com dados exigidos pelo confirm_input_schema

manus status [task_id]               # status da última tarefa (ou de um task_id específico)
manus result [task_id]               # última resposta do agente
manus history [limite]               # lista as tarefas recentes (padrão: 20)
manus open [task_id]                 # abre a tarefa no navegador
manus doctor                         # diagnóstico: versão, API key, conectividade, config

git diff | manus "revisa isso"       # lê stdin como parte do prompt
manus --json "prompt"                # stdout é só uma linha JSON (task_id/status/content/attachments/status_detail/error_detail), pra scripts
```

Variável de ambiente `MANUS_API_KEY` tem prioridade sobre a key salva em disco (útil em CI).

Flags globais:
- `--timeout <segundos>` (padrão 300) — orçamento total de espera pela tarefa (não por chamada individual)
- `--connector <nome-ou-uuid>` (repetível) — connector pra habilitar nessa mensagem; nome é resolvido via `connector.list`, ambíguo ou desconhecido dá erro claro
- `--allow-secret` — desliga o filtro de segredo em `--file`/`--project`/`@menção`
- `--no-gitignore` — ignora o `.gitignore` do projeto em `--project`
- `--dry-run` — só com `--project`: mostra o que seria enviado, sem chamar a API

Qualquer arquivo que o Manus anexar na resposta (código, planilha, imagem etc.) é baixado automaticamente pra `./manus-output/<task_id>/` — nunca sobrescreve um arquivo já existente (gera `nome (2).ext` etc.) e nunca ultrapassa 200MB por download.

### Ciclo de vida da tarefa e exit codes

Toda chamada que executa um turno (prompt direto, `--continue`, `--file`, `--project`, cada mensagem do REPL) termina num destes três estados, e o exit code reflete isso — nunca retorna `0` silenciosamente em `waiting`/`error`:

| status | significado | exit code |
|---|---|---|
| `stopped` | terminou com sucesso, resposta impressa | `0` |
| `waiting` | parou esperando uma ação sua | `2` |
| `error` | falhou, `error_message` da API impresso | `1` |

Quando `waiting`, a CLI mostra `waiting_for_event_type`/`waiting_for_event_id`/descrição e o que fazer a seguir:
- `waiting_for_event_type == "messageAskUser"` → responda normal (próxima mensagem via `task.sendMessage`)
- qualquer outro tipo → `manus confirm <event_id> [--input '<json>']` (ou `/confirm` no REPL)

### Dentro do modo chat (`manus`)

- Ao digitar `@`, um dropdown sugere arquivos permitidos do projeto, respeitando `.gitignore` e sem expor segredos, scripts ou symlinks. `@arquivo.py` na mensagem anexa o arquivo automaticamente (ex: `revise @app.py`).
- Ao começar a linha com `/`, um dropdown sugere `/status`, `/use <id>`, `/history`, `/open [id]`, `/confirm <event_id> [json]`, `/help` e `/exit`.

## Robustez (retry, rate limits, downloads)

- **Retry só onde é seguro**: `task.create`/`task.sendMessage`/`task.confirmAction`/o POST de `file.upload` nunca são repetidos numa falha ambígua (timeout depois de enviar a requisição) — repetir poderia duplicar uma ação real do agente. São repetidos apenas em erro de conexão (nunca chegou a sair do cliente) ou `429` (o servidor rejeitou sem processar). Chamadas idempotentes (`GET`, `PUT` no upload presignado) toleram retry em qualquer falha transitória, com backoff exponencial + jitter, respeitando `Retry-After` quando presente.
- **`file.upload`** é limitado a 40/min pela API — o envio de `--project` pausa automaticamente pra não estourar isso.
- **Downloads de anexo** vão pra um arquivo temporário e só são renomeados atomicamente no final; abortam (sem deixar lixo) se passarem de 200MB.

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

Cobre todos os subcomandos (`login use history open alias connector confirm doctor status result`) e as flags do comando padrão.

## `.manusrc` por projeto

Crie um `.manusrc` (JSON) na raiz do repo pra fixar a tarefa/connectors padrão desse projeto — sem precisar rodar `manus use` toda vez que entrar na pasta:

```json
{
  "task_id": "hpxhrG09FTWCzJ2mYeSyF6",
  "connectors": ["356d5bc1-fb9f-4fa1-babb-05039dc09d11"],
  "connector_names": ["GitHub"]
}
```

`connectors` espera UUIDs diretos (o formato que a API exige); `connector_names` aceita nomes e é resolvido do mesmo jeito que `--connector` (via `connector.list`). Campos desconhecidos ou com tipo errado fazem a CLI recusar o arquivo com um erro claro, em vez de ignorar silenciosamente.

Prioridade de tarefa: `--continue` explícito > `--task <apelido>` > `.manusrc` > tarefa nova.

## Onde fica salvo

- `~/.config/manus/credentials.json` — sua API key (permissão `600` desde a criação, escrita atômica)
- `~/.config/manus/state.json` — última tarefa e apelidos (usado por `--continue`, `status`, `result`, `alias`)

Ambos os arquivos são escritos atomicamente (arquivo temporário + rename) — uma escrita interrompida nunca deixa um JSON pela metade. Se algum dos dois ficar corrompido por fora da CLI, os comandos falham com uma mensagem clara em vez de travar com traceback.

No Windows esses arquivos ficam em `%USERPROFILE%\.config\manus\` (mesmo comportamento, via `pathlib.Path.home()`).

## Estrutura

```
manus_cli/
  cli.py          # parsing (argparse por subcomando), dispatch, exit codes — fino, sem lógica de negócio
  task_runner.py  # create/send + poll, lifecycle (stopped/waiting/error), confirmAction
  files.py        # seleção segura de arquivos (.gitignore, symlink, segredo, tamanho), upload/download em lote
  api.py          # só HTTP: auth, retry/backoff, validação de resposta
  config.py       # persistência atômica de credentials/state/.manusrc
  render.py       # apresentação (rich): tema, tabelas, markdown
tests/
  _helpers.py         # isola MANUS_CONFIG_DIR em temp dir — nenhum teste toca ~/.config/manus
  test_api.py         # retry policy, erros HTTP, download seguro, ciclo de vida do client
  test_task_runner.py # polling paginado, eventos malformados, timeout global, waiting/error/confirm
  test_files.py       # .gitignore, symlink, segredo, colisão de nome, limites, pacing de upload
  test_config.py      # escrita atômica, permissão, JSON corrompido, validação de .manusrc
  test_cli.py         # menções @, comandos /, resolução de connector, exit codes, disciplina do --json
packaging/
  requirements.txt      # pins de build (pyinstaller, pip-licenses) — renovado via Dependabot
  pyinstaller/
    entry.py             # ponto de entrada usado pelo PyInstaller pra empacotar manus_cli.cli:main
npm/
  manus-cli/                  # pacote launcher publicado (bin/manus.js) + testes Node
  manus-cli-<plataforma>/     # um pacote por SO/arquitetura, cada um só com o binário nativo em bin/
scripts/
  check-npm-version-drift.js  # valida que VERSION == version de todo package.json em npm/
docs/superpowers/specs/
  *.md            # spec de design do projeto
```

## Rodando os testes

```bash
python -m unittest discover -s tests -v
```

106 testes, todos com rede mockada via `httpx.MockTransport` ou client mockado (nada bate na API real) e config isolada em diretório temporário (nenhum teste toca `~/.config/manus`). Cobrem: retry idempotente vs. não-idempotente (o ponto mais importante — nunca duplicar `task.create`/`sendMessage`), `429`+`Retry-After`+jitter, corpo HTTP inválido/inesperado, ciclo de vida `stopped`/`waiting`/`error`, `.gitignore`, symlink escapando da raiz, filtro de segredo (inclusive no autocomplete e em `@menção`), dropdown de `/` e `@`, fallback ASCII em consoles Windows, colisão de nome, limites de tamanho/quantidade, download seguro, config/`.manusrc` corrompidos, resolução de connector por nome, e que `--json` só imprime JSON no stdout.

Roda automaticamente no GitHub Actions a cada push/PR: testes (Linux/macOS/Windows, Python 3.9 e 3.12), lint (`ruff`), e build+smoke do wheel.

### Testes e build do pacote npm

```bash
cd npm/manus-cli && node --test    # testes do launcher: seleção de plataforma, pacote nativo ausente, argv/exit/sinais
node scripts/check-npm-version-drift.js  # confere VERSION contra todo package.json em npm/
```

O workflow `.github/workflows/npm-release.yml` está configurado para buildar o binário nativo (PyInstaller) em runners macOS (arm64: `macos-14`, x64: `macos-15-intel`), Linux (x64/arm64) e Windows por push/PR, rodar `manus --help` e um comando offline real em cada um, empacotar com `npm pack` e instalar o tarball num diretório temporário. Antes de buildar/publicar, o próprio workflow roda a suite Python (`unittest`), `ruff`, `mypy` e um smoke test do wheel (`python-quality-gate`) — build e publish dependem desse job, então quebra de teste, lint ou tipagem bloqueia a tag. A primeira execução real nos runners ainda deve ser confirmada antes do release.

Cada tarball inclui `LICENSE`; os 5 pacotes nativos também incluem `THIRD_PARTY_NOTICES.txt`, gerado no CI (`pip-licenses`) com o inventário de licenças de tudo que o PyInstaller embutiu no binário. O `npm pack --dry-run` do CI falha se algum desses arquivos não aparecer no tarball (checagem positiva, não só grep de exclusão).

A publicação (`npm publish --provenance`) só roda em push de tag `vX.Y.Z`, e só depois de validar que a tag bate exatamente com o arquivo `VERSION`. Publica os 5 pacotes nativos primeiro e o launcher `manus-cli` por último (pra nunca deixar o launcher no ar apontando pra uma `optionalDependency` que ainda não existe no registry), e pula (sem erro) qualquer pacote cuja versão já esteja publicada — reexecutar o workflow após uma falha parcial é seguro.

**Publicação via OIDC (trusted publishing), sem `NPM_TOKEN`:** o job `publish` usa `permissions: id-token: write` e `npm publish --provenance`, sem token de longa duração. Isso exige que cada um dos 6 pacotes já esteja configurado como *Trusted Publisher* em npmjs.com (Settings → Trusted Publisher) apontando pra este repositório e pra este workflow. **Bootstrap**: um pacote novo, que nunca foi publicado, não pode virar trusted publisher antes de existir — a primeira publicação de cada pacote precisa ser feita manualmente por um mantenedor (`npm publish` local, autenticado) antes de configurar o trusted publisher; depois disso, toda publicação segue por este workflow via OIDC.

### Escolha do empacotador: PyInstaller

Os binários nativos são gerados com **PyInstaller** (`--onefile`), que embute o interpretador Python e todas as dependências (`httpx`, `rich`) num único executável — quem instala via npm não precisa ter Python. Alternativas consideradas:
- **Nuitka** (compila pra C): binários potencialmente menores/mais rápidos, mas exige toolchain de compilador C por plataforma no CI, aumentando a superfície de falha sem ganho relevante pra uma CLI de I/O de rede.
- **`shiv`/`zipapp`**: geram um `.pyz` que ainda depende de um Python instalado na máquina alvo — não atende ao requisito de rodar sem Python.
- **PyOxidizer**: projeto sem manutenção ativa desde 2023 — risco de longo prazo.

PyInstaller é maduro, tem builds nativos (sem cross-compile) em cada runner do GitHub Actions, e já foi validado localmente (macOS arm64): `manus --help` e `manus --project . --dry-run` rodando a partir do binário standalone, fora de qualquer venv.

## Problema conhecido (aberto com o suporte do Manus) — e o workaround

No período em que isso foi investigado, `task.create` respondia com sucesso (`task_id`/`task_url` válidos) mas a tarefa não era persistida do lado do Manus — `task.detail`/`task.listMessages`/`task.list` retornavam `not_found` logo em seguida. Reportado para `api-support@manus.ai`. **Isolado**: o problema era só na criação via API; tarefas criadas pela interface web sempre funcionaram normalmente via API (`task.detail`, `task.sendMessage`, `task.listMessages`).

**Workaround**: crie uma tarefa qualquer pela interface web (manus.im), pegue o `task_id` da URL (`https://manus.im/app/<task_id>`), e:

```bash
manus use <task_id>          # fixa essa tarefa como a atual
manus --continue "prompt"    # conversa nela via API normalmente
```

A CLI nunca sobrescreve um `task_id` bom por um que falhou ao criar — mesmo que `manus` sem `--continue` seja usado por engano, a última tarefa válida fica preservada.

## Licença

[MIT](LICENSE).

## Fora de escopo

Manus Projects (agrupamento de tarefas com instrução compartilhada), webhooks (fazem mais sentido para automação de longa duração do que para uma CLI interativa), `task.stop`/`task.delete`/paginação avançada de `history` (endpoints existem na doc mas não foram priorizados nesta rodada), modo verbose/debug expondo `request_id` por chamada.
