# Contribuindo com o manus-cli

Guia de desenvolvimento: estrutura do repositório, testes, empacotamento e o processo de release. Para instalação e uso como usuário final, veja o [README](README.md).

## Estrutura

```
manus_cli/
  cli.py          # parsing (argparse por subcomando), dispatch, exit codes, REPL + autocomplete — fino, sem lógica de negócio
  task_runner.py  # create/send + poll, lifecycle (stopped/waiting/error), confirmAction
  files.py        # seleção segura de arquivos (.gitignore, symlink, segredo, tamanho), upload/download em lote
  api.py          # só HTTP: auth, retry/backoff, validação de resposta
  config.py       # persistência atômica de credentials/state/.manusrc
  render.py       # apresentação (rich): tema, tabelas, markdown, fallback ASCII de glifos
tests/
  _helpers.py         # isola MANUS_CONFIG_DIR em temp dir — nenhum teste toca ~/.config/manus
  test_api.py         # retry policy, erros HTTP, download seguro, ciclo de vida do client
  test_task_runner.py # polling paginado, eventos malformados, timeout global, waiting/error/confirm
  test_files.py       # .gitignore, symlink, segredo, colisão de nome, limites, pacing de upload
  test_config.py      # escrita atômica, permissão, JSON corrompido, validação de .manusrc
  test_cli.py         # menções @, comandos /, resolução de connector, exit codes, disciplina do --json
  test_render.py      # detecção de encoding e fallback ASCII dos glifos Unicode
packaging/
  requirements.txt      # pins de build (pyinstaller, pip-licenses) — renovado via Dependabot
  pyinstaller/entry.py   # ponto de entrada usado pelo PyInstaller pra empacotar manus_cli.cli:main
npm/
  manus-cli/                  # pacote launcher publicado (bin/manus.js) + testes Node
  manus-cli-<plataforma>/     # um pacote por SO/arquitetura, cada um só com o binário nativo em bin/
scripts/
  check-npm-version-drift.js  # valida que VERSION == version de todo package.json em npm/
.github/workflows/
  tests.yml            # suite Python (Linux/macOS/Windows × 3.9/3.12), lint, build do wheel
  npm-release.yml       # build PyInstaller multiplataforma, empacotamento e publish npm por tag
docs/superpowers/specs/
  *.md            # spec de design do projeto
```

## Robustez (retry, rate limits, downloads)

- **Retry só onde é seguro**: `task.create`/`task.sendMessage`/`task.confirmAction`/o POST de `file.upload` nunca são repetidos numa falha ambígua (timeout depois de enviar a requisição) — repetir poderia duplicar uma ação real do agente. São repetidos apenas em erro de conexão (nunca chegou a sair do cliente) ou `429` (o servidor rejeitou sem processar). Chamadas idempotentes (`GET`, `PUT` no upload presignado) toleram retry em qualquer falha transitória, com backoff exponencial + jitter, respeitando `Retry-After` quando presente.
- **`file.upload`** é limitado a 40/min pela API — o envio de `--project` pausa automaticamente pra não estourar isso.
- **Downloads de anexo** vão pra um arquivo temporário e só são renomeados atomicamente no final; abortam (sem deixar lixo) se passarem de 200MB.

## Rodando os testes

```bash
python -m unittest discover -s tests -v
```

106 testes, todos com rede mockada via `httpx.MockTransport` ou client mockado (nada bate na API real) e config isolada em diretório temporário (nenhum teste toca `~/.config/manus`). Cobrem: retry idempotente vs. não-idempotente (o ponto mais importante — nunca duplicar `task.create`/`sendMessage`), `429`+`Retry-After`+jitter, corpo HTTP inválido/inesperado, ciclo de vida `stopped`/`waiting`/`error`, `.gitignore`, symlink escapando da raiz, filtro de segredo (inclusive no autocomplete e em `@menção`), dropdown de `/` e `@`, fallback ASCII em consoles Windows, colisão de nome, limites de tamanho/quantidade, download seguro, config/`.manusrc` corrompidos, resolução de connector por nome, e que `--json` só imprime JSON no stdout.

Roda automaticamente no GitHub Actions a cada push/PR ([`tests.yml`](.github/workflows/tests.yml)): testes (Linux/macOS/Windows, Python 3.9 e 3.12), lint (`ruff`), tipagem (`mypy`) e build+smoke do wheel.

### Testes e build do pacote npm

```bash
cd npm/manus-cli && node --test    # testes do launcher: seleção de plataforma, pacote nativo ausente, argv/exit/sinais
node scripts/check-npm-version-drift.js  # confere VERSION contra todo package.json em npm/
```

O workflow [`npm-release.yml`](.github/workflows/npm-release.yml) builda o binário nativo (PyInstaller) em runners macOS (arm64: `macos-14`, x64: `macos-15-intel`), Linux (x64/arm64) e Windows a cada push/PR/tag, roda `manus --help` e um comando offline real em cada um, empacota com `npm pack` e instala o tarball num diretório temporário pra provar que `npx manus` funciona de fato naquela plataforma. Antes de buildar/publicar, o próprio workflow roda a suite Python (`unittest`), `ruff`, `mypy` e um smoke test do wheel (`python-quality-gate`) — build e publish dependem desse job, então quebra de teste, lint ou tipagem bloqueia a tag.

Cada tarball inclui `LICENSE`; os pacotes nativos também incluem `THIRD_PARTY_NOTICES.txt`, gerado no CI (`pip-licenses`) com o inventário de licenças de tudo que o PyInstaller embutiu no binário. O `npm pack --dry-run` do CI falha se algum desses arquivos não aparecer no tarball (checagem positiva, não só grep de exclusão).

**Requisitos mínimos dos binários nativos:**
- **macOS**: Apple Silicon compilado no runner `macos-14` (mínimo macOS 14/Sonoma); Intel no `macos-15-intel` (mínimo macOS 15/Sequoia) — sem `MACOSX_DEPLOYMENT_TARGET` customizado, o binário herda o mínimo do próprio runner de build.
- **Linux**: binário compilado em `ubuntu-latest`/`ubuntu-24.04-arm` — vinculado à **glibc**. Não funciona em distros musl-only (ex: Alpine) sem glibc compat.

### Processo de release

1. Atualize o arquivo `VERSION` na raiz (fonte única — `pyproject.toml` e todo `package.json` em `npm/` leem dele; `scripts/check-npm-version-drift.js` falha o CI se algo divergir).
2. Dê push na `main` com os testes passando.
3. Crie e envie a tag `vX.Y.Z` (precisa bater exatamente com `VERSION`):
   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```
4. A tag dispara o job `build-native` (os binários) e, se tudo passar, o job `publish`, que publica os pacotes nativos primeiro e o launcher `manus-cli` por último — nunca deixando o launcher no ar apontando pra uma `optionalDependency` que ainda não existe no registry. Publicar uma versão que já existe é um no-op seguro (idempotente): reexecutar o workflow após uma falha parcial não duplica nem quebra nada.

**Publicação via OIDC (trusted publishing), sem `NPM_TOKEN` de longa duração:** o job `publish` usa `permissions: id-token: write` e `npm publish --provenance`. Isso exige que cada pacote já esteja configurado como *Trusted Publisher* em npmjs.com (Settings do pacote → Trusted Publisher) apontando pra este repositório e workflow.

**Bootstrap de um pacote novo:** um pacote que nunca foi publicado não pode virar trusted publisher antes de existir (é preciso já existir no registry pra configurar o vínculo). A primeira publicação de cada pacote precisa ser feita manualmente por um mantenedor autenticado (`npm publish`, com 2FA/token com bypass explícito), usando os tarballs já buildados pelo CI daquela tag — depois disso, toda publicação futura daquele pacote segue via OIDC neste workflow, sem intervenção manual.

Se um pacote específico for rejeitado pelo registry (ex: detecção antispam num nome novo), os demais podem seguir publicados normalmente — o pendente é resolvido com o suporte do npm (https://npmjs.com/support) e publicado isoladamente depois, sem precisar de nova tag.

### Escolha do empacotador: PyInstaller

Os binários nativos são gerados com **PyInstaller** (`--onefile`), que embute o interpretador Python e todas as dependências (`httpx`, `rich`, `pathspec`, `prompt_toolkit`) num único executável — quem instala via npm não precisa ter Python. Alternativas consideradas:
- **Nuitka** (compila pra C): binários potencialmente menores/mais rápidos, mas exige toolchain de compilador C por plataforma no CI, aumentando a superfície de falha sem ganho relevante pra uma CLI de I/O de rede.
- **`shiv`/`zipapp`**: geram um `.pyz` que ainda depende de um Python instalado na máquina alvo — não atende ao requisito de rodar sem Python.
- **PyOxidizer**: projeto sem manutenção ativa desde 2023 — risco de longo prazo.

PyInstaller é maduro, tem builds nativos (sem cross-compile) em cada runner do GitHub Actions, e já foi validado em produção: build real + smoke test em macOS, Linux e Windows via CI, e instalação de ponta a ponta via `npm install manus-cli` a partir do registry público.

## Fora de escopo

Manus Projects (agrupamento de tarefas com instrução compartilhada), webhooks (fazem mais sentido para automação de longa duração do que para uma CLI interativa), `task.stop`/`task.delete`/paginação avançada de `history` (endpoints existem na doc mas não foram priorizados nesta rodada), modo verbose/debug expondo `request_id` por chamada.
