# Manus CLI — agente local de programação

## Objetivo

Adicionar `manus code` sem alterar o comportamento do chat existente. O novo comando usa o Manus como planejador e o CLI como executor local controlado: o agente lê o repositório, edita arquivos, inspeciona o diff e roda comandos de verificação até concluir a tarefa.

Sucesso significa conseguir entrar em um repositório e executar, por exemplo:

```bash
manus code "corrija os testes quebrados"
```

O agente deve descobrir o código relevante, aplicar mudanças confinadas à raiz escolhida, validar o resultado e apresentar resumo final com arquivos alterados, comandos executados e limites restantes.

## Restrições da API Manus

Structured Output é uma extração posterior ao término de cada turno, não function calling durante a execução. Portanto, cada turno do Manus produz exatamente uma decisão estruturada. O CLI executa essa decisão localmente, envia o resultado em um novo `task.sendMessage`, rearma o schema e repete.

Nenhuma alegação de “ferramenta nativa local do Manus” será feita. A ferramenta local pertence ao CLI.

## Interface de linha de comando

```text
manus code [PROMPT...]
  --root DIRETORIO          raiz do workspace; padrão: diretório atual
  --max-steps N             padrão: 30
  --command-timeout SEG     padrão: 120
  --approval MODE           supervised | balanced | autonomous
  --yes                     aprova ações confirmáveis em execução não interativa
  --json                    saída final estruturada
  --agent-profile PROFILE   perfil Manus usado na tarefa
```

`balanced` será o padrão:

- leitura, busca, edição dentro do workspace, `git diff` e verificações conhecidas são automáticas;
- instalação, rede, publicação, exclusão e comandos potencialmente destrutivos pedem confirmação;
- violações duras — saída do workspace, segredo, `.git`, shell indireto ou comando destrutivo proibido — são bloqueadas mesmo com `--yes`;
- em stdin não interativo, ação que precisaria de confirmação falha de forma explícita, salvo uso de `--yes`.

`supervised` confirma toda escrita e execução. `autonomous` remove confirmações permitidas, mas mantém bloqueios duros.

## Arquitetura

### `CodingAgent`

Module profundo que expõe uma interface pequena: recebe tarefa e opções, devolve `AgentResult`. Internamente controla task id, schema, passos, chamadas ao Manus, execução de ferramentas, limite de tempo e finalização. CLI não conhece o protocolo interno.

Invariantes:

- no máximo uma ação local por turno;
- no máximo `max_steps` por execução;
- resultado de toda ação retorna ao mesmo task id;
- falha de ferramenta é observável pelo Manus e pode ser corrigida em passo posterior;
- falha de protocolo não executa ação parcial;
- conclusão inclui resumo e estado de validação.

### `ManusCodingAdapter`

Adapter sobre `ManusClient` e `task_runner`. Responsável por:

- criar/continuar tarefa com prompt de protocolo;
- armar o JSON Schema em cada turno;
- extrair e validar `structured_output_result`;
- distinguir `action`, `final` e erro de extração;
- manter detalhes HTTP fora do núcleo do agente.

O resultado estruturado terá forma fechada:

```json
{
  "kind": "action",
  "summary": "Ler configuração do projeto",
  "tool": "read_file",
  "arguments_json": "{\"path\":\"pyproject.toml\"}",
  "final_message": ""
}
```

Todos os campos são obrigatórios por compatibilidade com o subconjunto de JSON Schema da API. `arguments_json` é decodificado e validado novamente pelo CLI; campos inesperados são rejeitados.

### `WorkspaceTools`

Uma interface `execute(tool_name, arguments) -> ToolResult` esconde despacho, contenção, limites e formatação. Ferramentas iniciais:

- `list_files`: lista arquivos relativos, com limite e filtros;
- `read_file`: lê trecho textual por linha, nunca arquivo inteiro sem limite;
- `search`: busca literal ou regex com resultado limitado;
- `write_file`: cria ou substitui arquivo textual atomicamente;
- `replace_text`: troca conteúdo exato, exigindo ocorrência única por padrão;
- `git_diff`: retorna status/diff limitado, sem modificar Git;
- `run_command`: executa argv sem shell, cwd relativo e ambiente sanitizado.

Não haverá shell string. `run_command` recebe lista JSON de argumentos e usa `shell=False`. Saída guarda exit code, stdout, stderr, duração e indicação de truncamento.

### `PolicyEngine`

Module puro: recebe descrição da ação e retorna `allow`, `confirm` ou `deny`, com motivo. Não lê input nem executa processos. Isso permite testar toda matriz de segurança sem rede ou subprocessos.

Bloqueios duros:

- caminhos absolutos, `..`, NUL, symlink escapando da raiz e qualquer alvo fora do workspace;
- leitura/escrita de padrões de segredo já reconhecidos pelo projeto, além de `.env`, credenciais e chaves privadas;
- escrita dentro de `.git`;
- shells e avaliação indireta (`sh -c`, `bash -c`, `zsh -c`, PowerShell command strings);
- `sudo`, publicação de pacote, push, reset/clean destrutivo, remoção recursiva e equivalentes;
- executáveis fora da política definida.

Ações confirmáveis incluem instalação de dependência, comandos com rede, migrations e exclusão confinada ao workspace. Teste, lint, typecheck, build local e inspeção Git pertencem à classe automática do modo equilibrado.

### Adapters externos

- `SubprocessCommandAdapter`: subprocesso real, timeout, captura limitada e kill do grupo quando suportado;
- `ApprovalAdapter`: prompt interativo ou adapter previsível nos testes;
- fakes em memória para Manus, ferramentas e aprovação.

Dependências apontam para dentro: CLI monta adapters; `CodingAgent` depende apenas das interfaces; política e modelos não importam Rich, httpx ou prompt-toolkit.

## Fluxo

1. Resolver e validar raiz do workspace.
2. Criar tarefa Manus com protocolo, objetivo, ferramentas disponíveis e regras.
3. Aguardar término do turno e obter decisão estruturada.
4. Validar schema, nome da ferramenta e argumentos.
5. Classificar ação na política.
6. Bloquear, confirmar ou executar.
7. Enviar `ToolResult` textual e estruturado ao mesmo task id.
8. Repetir até `final`, erro fatal, cancelamento ou limite de passos.
9. Mostrar resumo local verdadeiro, complementando a mensagem do modelo com diff e comandos realmente observados.

## Robustez e desempenho

- escrita atômica por arquivo temporário no mesmo diretório e `os.replace`;
- tamanho máximo de leitura e saída; conteúdo binário rejeitado;
- paginação/trechos em vez de despejar arquivos grandes;
- resultados de ferramentas truncados com metadado explícito;
- timeout por comando e orçamento total do turno Manus já existente;
- `MANUS_API_KEY` e variáveis de credencial conhecidas removidas do ambiente filho;
- cancelamento por Ctrl+C encerra subprocesso local e não deixa escrita intermediária;
- logs não contêm conteúdo de segredo nem chave;
- nenhuma dependência runtime nova será adicionada se a biblioteca padrão resolver o problema.

## Erros

- erro recuperável de ferramenta retorna ao agente como `ok: false`;
- JSON inválido, ferramenta desconhecida ou argumentos inválidos contam como passo e permitem nova tentativa, até limite pequeno de erros consecutivos;
- falha de Structured Output informa erro da extração e não usa valor fallback;
- timeout e cancelamento produzem exit code não zero;
- `final` sem qualquer validação informa claramente “não validado”;
- `--json` mantém stdout exclusivamente JSON; progresso e confirmações vão para stderr.

## Testes

1. Política: traversal, symlink escape, segredos, `.git`, comandos indiretos/destrutivos e matriz dos três modos.
2. Ferramentas: leitura parcial, busca limitada, escrita atômica, replace ambíguo, diff, timeout, truncamento, cwd e sanitização de ambiente.
3. Adapter Manus: extração bem-sucedida, `success: false`, schema ausente, payload inválido e rearme em múltiplos turnos.
4. Orquestrador: ação→resultado→ação→final, recuperação de erro, confirmação, cancelamento, limite de passos e resumo verdadeiro.
5. CLI: parsing, stdin não interativo, `--yes`, `--json` e preservação dos comandos atuais.
6. Integração offline: repositório temporário + Manus fake, com edição real e comando de teste real sem rede.
7. Gates existentes: unittest, ruff, mypy, wheel/PyInstaller e launcher Node.

## Documentação e distribuição

README receberá instalação npm/pipx já existente mais seção `manus code`, exemplos, modelo de aprovação e avisos de confiança em código de terceiros. Completions bash/zsh incluirão subcomando e flags. O pacote npm continuará sendo launcher do mesmo núcleo Python; nenhuma segunda implementação Node será criada.

## Fora de escopo inicial

- terminal PTY interativo;
- comandos shell compostos, pipes e redirecionamentos;
- acesso fora do workspace;
- edição binária;
- rollback automático de mudanças anteriores à execução;
- publicação automática, push ou migrations sem confirmação explícita;
- compatibilidade com outro provedor de modelo.
