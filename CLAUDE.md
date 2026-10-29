# CLAUDE.md

## Visão geral

Software factory experimental (fase de teste). O objetivo é automatizar o ciclo
**issue → plano de implementação → implementação → Pull Request**:

1. Criar uma issue no Linear ou pegar uma issue já existente.
2. Gerar um plano de implementação a partir da issue.
3. Executar o plano (implementar o código).
4. Abrir a PR com as mudanças.

## Stack definida

- **Linguagem:** Python 3.12+ (pacote `software_factory` em `src/`, build com setuptools, ambiente com `uv`).
- **Orquestrador:** [Temporal](https://docs.temporal.io/develop/python) (`temporalio` SDK). Cada etapa do fluxo deve ser modelada como workflow/activities.
- **Harness de execução:** Claude Code — é quem efetivamente planeja e implementa as mudanças, invocado a partir das activities do Temporal.
- **Fonte de tarefas:** Linear (issues), recebidas via webhook.
- **HTTP:** FastAPI + uvicorn (servidor de webhooks).

Ainda **não definidos** (não assumir sem confirmar com o usuário): forma de
invocar o Claude Code (CLI headless, Agent SDK etc.), acesso à API do Linear
(SDK/GraphQL/MCP) para além do webhook, provedor de Git/PR, estratégia de workspace (clone, worktree,
container), persistência e deploy. Ao introduzir qualquer tecnologia nova,
pergunte antes e atualize esta seção.

## Estrutura

```
src/software_factory/
  __init__.py         # __version__
  __main__.py         # entrypoint (script `software-factory`): carrega .env e sobe o uvicorn
  api.py              # create_app(): FastAPI com /health e routers
  config.py           # Settings lidas de env (LINEAR_WEBHOOK_SECRET, HOST, PORT)
  linear/
    signature.py      # validação HMAC do Linear-Signature + anti-replay
    events.py         # LinearIssue, should_process (filtro por label), parse_issue
    webhook.py        # POST /webhooks/linear; handle_issue = ponto de extensão (só loga)
tests/                # unittest; payloads.py tem fixtures no formato do Linear
```

## Webhook do Linear

- Gatilho: issue `create` com label `Feature` ou `Bug` (case-insensitive), ou `update`
  em que uma dessas labels foi adicionada (`updatedFrom.labelIds`).
- Eventos ignorados retornam 200 para o Linear não reenviar.
- Ainda **fora do escopo**: workflow/processamento — `handle_issue` em
  `linear/webhook.py` só loga; é ali que entrará o `start_workflow` do Temporal.
- Não há varredura de issues existentes em TODO (projeto novo, sem backlog).

## Comandos

```bash
# setup
uv venv .venv && source .venv/bin/activate && uv pip install -e ".[dev]"

# servidor Temporal local (terminal separado) — gRPC localhost:7233, UI http://localhost:8233
temporal server start-dev

# servidor de webhooks (lê .env)
software-factory
# ou, com reload
uvicorn software_factory.api:create_app --factory --env-file .env --reload

# túnel para o Linear alcançar a máquina local
ngrok http 8000

# testes
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Convenções para o Temporal

- **Workflows devem ser determinísticos:** nada de I/O, rede, `datetime.now()`,
  `random`, threads ou chamadas ao Claude Code/Linear/Git dentro do workflow.
  Use `workflow.now()`, `workflow.random()` e mova todo efeito colateral para activities.
- Toda interação externa (Linear, Claude Code, Git, criação de PR) fica em **activities**,
  com timeouts e retry policy explícitos. Execuções do Claude Code são longas:
  configure `start_to_close_timeout` adequado e use heartbeat.
- Activities devem ser **idempotentes** (podem ser reexecutadas em retry), ex.: checar se a
  branch/PR já existe antes de criar.
- Entradas/saídas de workflows e activities como `dataclass`es serializáveis.
- Use o sandbox padrão do SDK Python; importe módulos não determinísticos com
  `workflow.unsafe.imports_passed_through()` quando necessário.
- Teste workflows com `temporalio.testing.WorkflowEnvironment` (time-skipping) e activities mockadas.

## Diretrizes gerais

- Segredos (tokens do Linear, GitHub, Anthropic) vêm de variáveis de ambiente / `.env`
  (já no `.gitignore`); nunca commitar.
- Manter o README e este arquivo atualizados quando comandos ou stack mudarem.
- Branch principal: `main`.
