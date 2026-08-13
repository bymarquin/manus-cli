# manus-cli

[![tests](https://github.com/bymarquin/manus-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/bymarquin/manus-cli/actions/workflows/tests.yml)
[![npm-release](https://github.com/bymarquin/manus-cli/actions/workflows/npm-release.yml/badge.svg)](https://github.com/bymarquin/manus-cli/actions/workflows/npm-release.yml)
[![npm version](https://img.shields.io/npm/v/manus-cli.svg)](https://www.npmjs.com/package/manus-cli)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

CLI não-oficial para a [API do Manus](https://open.manus.ai/docs/v2/introduction) (v2). Cria e acompanha tarefas do Manus direto do terminal, com modo chat contínuo, autocomplete de `/` comandos e `@` arquivos, upload seguro de projetos inteiros e download automático de anexos.

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

O núcleo do `manus-cli` é sempre o mesmo código Python, independente de como você instala. `pipx` roda esse código com o Python do seu sistema; `npm` baixa um binário standalone (compilado com PyInstaller) que embute o próprio interpretador, então **não exige Python instalado**. O comportamento do comando `manus` é idêntico nas duas.

### pipx (recomendado se você já tem Python)

```bash
python3 -m pip install --user pipx && python3 -m pipx ensurepath   # Windows: py -m pip install --user pipx && py -m pipx ensurepath
# reabra o terminal, depois:
git clone https://github.com/bymarquin/manus-cli.git
cd manus-cli
pipx install .
```

### npm (recomendado se você já tem Node.js, sem precisar de Python)

```bash
npm install -g manus-cli
manus login
```

Ou sem instalar globalmente: `npx manus-cli login`.

O pacote `manus-cli` do npm é um **launcher fino**: detecta seu sistema operacional/arquitetura e executa o binário nativo correto (instalado como `optionalDependency`). Nenhuma lógica de negócio vive no lado Node.

| Plataforma | Status |
|---|---|
| macOS (Apple Silicon / Intel) | ✅ publicado |
| Linux x64 / arm64 | ✅ publicado |
| Windows x64 | ⏳ pendente — bloqueado por uma revisão antispam do registry do npm; use pipx no Windows até isso ser liberado |

Fora dessa lista (Linux 32-bit, FreeBSD etc.), o launcher falha com um erro claro — use pipx.

**Troubleshooting do npm:**
- `pacote nativo "..." não foi instalado` → reinstale sem `--no-optional`/`--omit=optional`.
- `plataforma não suportada` → não existe binário pra sua combinação de SO/arquitetura; use pipx.

Detalhes de compatibilidade (versão mínima de macOS, glibc no Linux) e o processo de release estão no [CONTRIBUTING.md](CONTRIBUTING.md).

### venv (sem instalar globalmente, requer Python)

```bash
git clone https://github.com/bymarquin/manus-cli.git && cd manus-cli && python3 -m venv .venv
.venv/bin/pip install -e .   # Windows: .venv\Scripts\pip install -e .
```

## Uso

```bash
manus login                          # configura a API key (fica salva localmente)

manus "pesquise X e resuma"          # cria uma tarefa e espera o resultado
manus                                # modo chat contínuo (tela de conversa)
manus --continue "e sobre Y?"        # continua a última tarefa
manus --file relatorio.pdf "resuma"  # anexa um arquivo
manus --project .                    # sobe os arquivos do diretório atual como contexto
manus --project . --dry-run          # só mostra o que seria enviado, sem enviar nada nem precisar de key

manus use <task_id> --as backend     # fixa uma tarefa existente e salva um apelido
manus --task backend "prompt"        # usa a tarefa do apelido "backend"

manus connector list                 # connectors da sua conta
manus --connector github "prompt"    # resolve "github" pro UUID do connector (ou passe o UUID direto)

manus confirm <event_id> [--input '{"foo":"bar"}']  # confirma uma ação pendente

manus status [task_id]               # status da última tarefa (ou de uma específica)
manus result [task_id]               # última resposta do agente
manus history [limite]               # tarefas recentes (padrão: 20)
manus doctor                         # diagnóstico: versão, API key, conectividade, config

git diff | manus "revisa isso"       # lê stdin como parte do prompt
manus --json "prompt"                # stdout é só uma linha JSON, pra scripts
```

Variável `MANUS_API_KEY` tem prioridade sobre a key salva em disco (útil em CI). Flags úteis: `--timeout <s>` (padrão 300), `--allow-secret` (desliga o filtro de segredo), `--no-gitignore`.

Anexos que o Manus devolver na resposta vão automaticamente pra `./manus-output/<task_id>/` (nunca sobrescreve, nunca passa de 200MB por arquivo).

**Exit codes** — nunca retorna `0` silenciosamente quando a tarefa não terminou bem:

| status | significado | exit code |
|---|---|---|
| `stopped` | terminou com sucesso | `0` |
| `waiting` | parou esperando uma ação sua (veja `manus confirm`) | `2` |
| `error` | falhou | `1` |

**No modo chat (`manus`):** `@` sugere arquivos do projeto (respeitando `.gitignore`, sem expor segredos/symlinks); `/` sugere comandos (`/status /use /history /open /confirm /help /exit`). Em terminais sem suporte a Unicode, os glifos caem automaticamente pra ASCII.

## `.manusrc` por projeto

Um `.manusrc` (JSON) na raiz do repo fixa a tarefa/connectors padrão desse projeto, sem precisar rodar `manus use` toda vez:

```json
{
  "task_id": "hpxhrG09FTWCzJ2mYeSyF6",
  "connectors": ["356d5bc1-fb9f-4fa1-babb-05039dc09d11"],
  "connector_names": ["GitHub"]
}
```

Prioridade de tarefa: `--continue` explícito > `--task <apelido>` > `.manusrc` > tarefa nova.

## Onde fica salvo

`~/.config/manus/credentials.json` (API key, permissão `600`) e `~/.config/manus/state.json` (última tarefa, apelidos) — ambos com escrita atômica. No Windows: `%USERPROFILE%\.config\manus\`.

## Autocomplete de shell

**Bash** (`~/.bashrc`): `source /caminho/pra/manus-cli/completions/manus.bash`
**Zsh** (`~/.zshrc`, antes do `compinit`): `fpath+=(/caminho/pra/manus-cli/completions)`

## Problema conhecido (aberto com o suporte do Manus)

Em alguns casos, `task.create` responde com sucesso mas a tarefa não é persistida do lado do Manus (`task.detail` retorna `not_found` logo em seguida). Reportado para `api-support@manus.ai`. Tarefas criadas pela interface web sempre funcionam normalmente via API.

**Workaround**: crie a tarefa pela interface web (manus.im), pegue o `task_id` da URL, e:

```bash
manus use <task_id>
manus --continue "prompt"
```

## Contribuindo

Estrutura do repositório, como rodar os testes, empacotamento (PyInstaller/npm) e o processo de release estão documentados em [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

[MIT](LICENSE).
