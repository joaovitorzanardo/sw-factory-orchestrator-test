# Transições de status no Linear — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O `PlanIssueWorkflow` move a issue no Linear de `To Do` para `In Planning` como primeiro passo, e para `In Progress` depois que o plano foi gerado e commitado.

**Architecture:** Um cliente GraphQL mínimo (`LinearClient`, `httpx` puro contra `https://api.linear.app/graphql`) e uma activity `move_issue` que lê o status atual e os status do time da issue, resolve o destino **pelo nome** e aplica `issueUpdate`. O workflow chama `move_issue` antes de criar o sandbox (com guarda `from_state="To Do"`) e depois do `kill_sandbox` (sem guarda). Em falha o status fica como está — sem compensação.

**Tech Stack:** Python 3.12, temporalio, httpx (passa de dev para runtime), API GraphQL do Linear, unittest.

**Spec:** conversa de 2026-10-30 (resumo): issue nova está em `To Do`; o primeiro passo do workflow é mover para `In Planning`; com o plano criado e commitado, mover para `In Progress`. Decisões: API via GraphQL com httpx + `LINEAR_API_KEY` (personal API key), status resolvidos por nome no time da issue; em falha deixa em `In Planning`; só move para `In Planning` se estiver em `To Do` (senão pula a transição, mas planeja e move para `In Progress` normalmente).

## Global Constraints

- Única mudança de dependência: `httpx>=0.27` sai de `[project.optional-dependencies].dev` e entra em `dependencies`. Nenhum SDK do Linear.
- Endpoint: `https://api.linear.app/graphql`; header `Authorization: <LINEAR_API_KEY>` (personal key, **sem** `Bearer`).
- Nomes dos status (constantes em `orchestration/models.py`): `STATE_TODO = "To Do"`, `STATE_IN_PLANNING = "In Planning"`, `STATE_IN_PROGRESS = "In Progress"`. Comparação de nomes case-insensitive.
- Status são procurados no time da própria issue (`issue.team.states`), não em todo o workspace.
- `LINEAR_API_KEY` é obrigatória para o worker (`FactorySettings`), não para o servidor de webhook.
- Workflow continua determinístico; chamadas ao Linear só na activity, com timeout e retry explícitos.
- `move_issue` é idempotente: issue já no status de destino → sucesso sem mutation.
- Logs, mensagens de erro e docs em português, como o resto do código.

## Review Focus

1. Status de destino não existe no time (renomeado, typo, outro time) — `ApplicationError` não-retentável cuja mensagem nomeia o status procurado; o workflow falha antes de criar sandbox. Teste em Task 2.
2. Retry depois de uma mutation que já foi aplicada (resposta perdida) — a issue já está no destino; deve retornar `True` sem nova mutation. Teste em Task 2.
3. Linear responde erro GraphQL (HTTP 200 ou 400 com `errors`) — não pode ser tratado como sucesso; erro de rate limit (`extensions.code == "RATELIMITED"`) deve ser retentável, os demais não. Teste em Task 1.
4. `LINEAR_API_KEY` inválida (HTTP 401) — falha não-retentável na primeira activity, não 5 retries. Teste em Task 1.
5. Issue apagada entre o webhook e o workflow (`issue: null`) — `ApplicationError` não-retentável. Teste em Task 2.

---

### Task 1: `LinearClient` GraphQL

**Files:**
- Create: `src/software_factory/linear/client.py`
- Modify: `pyproject.toml` (httpx para `dependencies`)
- Test: `tests/test_linear_client.py`

**Interfaces:**
- Produces:
  - `LINEAR_API_URL = "https://api.linear.app/graphql"`
  - `class LinearClient(api_key: str, transport: httpx.AsyncBaseTransport | None = None)`
  - `async LinearClient.execute(query: str, variables: dict[str, Any]) -> dict[str, Any]` — retorna o campo `data`; levanta `ApplicationError` (ver regras abaixo). Erros de rede (`httpx.TransportError`) propagam e o Temporal retenta.

Regras de erro, nesta ordem:
1. Corpo JSON com `errors` não vazio: se algum erro tem `extensions.code == "RATELIMITED"` → `ApplicationError` retentável; senão → não-retentável, mensagem com os `message` dos erros.
2. HTTP 429 ou ≥ 500 → retentável. Outro status fora de 2xx → não-retentável (inclui 401/403).

- [ ] **Step 1: Escrever os testes**

Use `httpx.MockTransport` com um handler que grava as requests e devolve respostas pré-definidas.

```python
async def test_sends_query_with_api_key(self) -> None:
    # resposta 200 {"data": {"viewer": {"id": "u1"}}}
    data = await client.execute("query { viewer { id } }", {"a": 1})
    self.assertEqual(data, {"viewer": {"id": "u1"}})
    request = requests[0]
    self.assertEqual(str(request.url), "https://api.linear.app/graphql")
    self.assertEqual(request.method, "POST")
    self.assertEqual(request.headers["Authorization"], "lin-key")
    self.assertEqual(json.loads(request.content), {"query": "query { viewer { id } }", "variables": {"a": 1}})

async def test_graphql_error_is_non_retryable(self) -> None:
    # 200 {"errors": [{"message": "Entity not found"}]}
    with self.assertRaises(ApplicationError) as ctx:
        await client.execute(QUERY, {})
    self.assertTrue(ctx.exception.non_retryable)
    self.assertIn("Entity not found", str(ctx.exception))

async def test_rate_limit_is_retryable(self) -> None:
    # 400 {"errors": [{"message": "rate limited", "extensions": {"code": "RATELIMITED"}}]}
    ...
    self.assertFalse(ctx.exception.non_retryable)

async def test_unauthorized_is_non_retryable(self) -> None:   # 401, corpo não-JSON
    ...
    self.assertTrue(ctx.exception.non_retryable)

async def test_server_error_is_retryable(self) -> None:       # 502, corpo não-JSON
    ...
    self.assertFalse(ctx.exception.non_retryable)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONPATH=src python3 -m unittest tests.test_linear_client -v`
Expected: FAIL com `ModuleNotFoundError: software_factory.linear.client`

- [ ] **Step 3: Implementar `LinearClient` em `src/software_factory/linear/client.py`**

Um `httpx.AsyncClient(transport=..., timeout=30)` por chamada (`async with`); corpo não-JSON é tratado como sem `errors`. Mover `httpx>=0.27` para `dependencies` no `pyproject.toml` e reinstalar (`uv pip install -e ".[dev]"`).

- [ ] **Step 4: Rodar e ver passar**

Run: `PYTHONPATH=src python3 -m unittest tests.test_linear_client -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Commit**

```bash
git add src/software_factory/linear/client.py tests/test_linear_client.py pyproject.toml
git commit -m "feat: cliente GraphQL mínimo para a API do Linear"
```

---

### Task 2: Activity `move_issue` e configuração do worker

**Files:**
- Create: `src/software_factory/orchestration/linear_activities.py`
- Modify: `src/software_factory/orchestration/models.py` (constantes de status + `MoveIssueRequest`)
- Modify: `src/software_factory/config.py` (`FactorySettings.linear_api_key`)
- Modify: `src/software_factory/orchestration/worker.py` (registrar a activity)
- Modify: `tests/test_activities.py` (`SETTINGS` ganha `linear_api_key="lin-key"`)
- Modify: `.env.example`
- Test: `tests/test_linear_activities.py`

**Interfaces:**
- Consumes: `LinearClient.execute(query, variables) -> dict` (Task 1).
- Produces:
  - Em `models.py`: `STATE_TODO`, `STATE_IN_PLANNING`, `STATE_IN_PROGRESS` (valores em Global Constraints) e
    ```python
    @dataclass(frozen=True)
    class MoveIssueRequest:
        issue_id: str
        to_state: str
        from_state: str | None = None  # guarda: só move se o status atual for este
    ```
  - `class LinearActivities(client: LinearClient)` com `@activity.defn async def move_issue(self, request: MoveIssueRequest) -> bool` — `True` se a issue terminou no destino (movida ou já estava), `False` se a guarda pulou.
  - `FactorySettings.linear_api_key: str` (campo obrigatório, logo após `target_repo`; `from_env` usa `_required("LINEAR_API_KEY")`).

Lógica de `move_issue`:
1. Query única:
   ```graphql
   query($id: String!) {
     issue(id: $id) { state { id name } team { states { nodes { id name } } } }
   }
   ```
   `issue` nulo → `ApplicationError` não-retentável.
2. Status atual == `to_state` → `True` (sem mutation).
3. `from_state` definido e status atual ≠ `from_state` → log info e `False`.
4. Destino não encontrado em `team.states` → `ApplicationError` não-retentável com o nome procurado e os disponíveis.
5. `mutation($id: String!, $stateId: String!) { issueUpdate(id: $id, input: {stateId: $stateId}) { success } }`; `success` falso → `ApplicationError` (retentável). Retorna `True`.

- [ ] **Step 1: Escrever os testes**

`LinearActivities(LinearClient("lin-key", transport=MockTransport(...)))` rodando via `ActivityEnvironment().run(...)`. Fixture do time: estados `To Do`/`state-todo`, `In Planning`/`state-planning`, `In Progress`/`state-progress`, `Done`/`state-done`.

```python
async def test_moves_from_todo_to_in_planning(self) -> None:
    # issue em "To Do"; segunda resposta {"data": {"issueUpdate": {"success": true}}}
    moved = await run(MoveIssueRequest("issue-1", to_state="In Planning", from_state="To Do"))
    self.assertTrue(moved)
    self.assertEqual(len(requests), 2)
    self.assertEqual(json.loads(requests[1].content)["variables"], {"id": "issue-1", "stateId": "state-planning"})

async def test_skips_when_not_in_from_state(self) -> None:
    # issue em "Done"
    self.assertFalse(await run(MoveIssueRequest("issue-1", to_state="In Planning", from_state="To Do")))
    self.assertEqual(len(requests), 1)

async def test_already_in_target_state_is_noop(self) -> None:
    # issue em "in progress" (minúsculo): também testa case-insensitive
    self.assertTrue(await run(MoveIssueRequest("issue-1", to_state="In Progress")))
    self.assertEqual(len(requests), 1)

async def test_moves_without_guard(self) -> None:
    # issue em "In Planning" → "In Progress", sem from_state
    ...  # stateId == "state-progress"

async def test_unknown_target_state_is_non_retryable(self) -> None:
    # time sem "In Planning"
    ...
    self.assertTrue(ctx.exception.non_retryable)
    self.assertIn("In Planning", str(ctx.exception))

async def test_missing_issue_is_non_retryable(self) -> None:
    # {"data": {"issue": null}}
    ...

async def test_unsuccessful_update_raises(self) -> None:
    # issueUpdate.success == false
    with self.assertRaises(ApplicationError): ...
```

E em `tests/test_activities.py` (ou um novo `tests/test_config.py`, se preferir isolar): `FactorySettings.from_env` sem `LINEAR_API_KEY` → `RuntimeError`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONPATH=src python3 -m unittest tests.test_linear_activities -v`
Expected: FAIL com `ModuleNotFoundError: software_factory.orchestration.linear_activities`

- [ ] **Step 3: Implementar `MoveIssueRequest`, constantes, `LinearActivities.move_issue` e `FactorySettings.linear_api_key`**

No `worker.py`: `linear = LinearActivities(LinearClient(settings.linear_api_key))` e incluir `linear.move_issue` em `activities=[...]`. No `.env.example`, na seção do worker:
```
# Personal API key do Linear (Settings → Security & access → Personal API keys),
# com permissão de escrita em issues (move o status da issue)
LINEAR_API_KEY=
```

- [ ] **Step 4: Rodar a suíte inteira**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests`
Expected: PASS (inclui `test_activities` com o `SETTINGS` atualizado)

- [ ] **Step 5: Commit**

```bash
git add src/software_factory/orchestration/linear_activities.py src/software_factory/orchestration/models.py \
  src/software_factory/config.py src/software_factory/orchestration/worker.py \
  tests/test_linear_activities.py tests/test_activities.py .env.example
git commit -m "feat: activity move_issue muda o status da issue no Linear"
```

---

### Task 3: Workflow move a issue e docs

**Files:**
- Modify: `src/software_factory/orchestration/workflow.py`
- Modify: `tests/test_workflow.py`
- Modify: `CLAUDE.md`, `README.md`

**Interfaces:**
- Consumes: activity `"move_issue"` com `MoveIssueRequest` → `bool`; constantes `STATE_*` (Task 2).
- Produces: nova ordem do `PlanIssueWorkflow`:
  `move_issue(To Do → In Planning)` → `create_sandbox` → `clone_repo` → `generate_plan` → `commit_plan` → `kill_sandbox` (finally) → `move_issue(→ In Progress)`.
  `PlanResult` não muda.

`move_issue` nas duas chamadas: `start_to_close_timeout=timedelta(seconds=30)`, `RetryPolicy(maximum_attempts=5)`, `result_type=bool`. A primeira fica **fora** do `try` (falhou → nada de sandbox). A segunda roda depois do `finally` (sandbox já destruído); o retorno `False` da primeira só é logado (`workflow.logger.info`), não interrompe.

- [ ] **Step 1: Atualizar os testes**

`FakeActivities` ganha `move_issue` (nome `"move_issue"`) que grava `f"move_issue:{request.to_state}"` em `calls`, guarda os requests em `self.moves` e aceita `skip_planning_move: bool` (retorna `False` para `In Planning`) e `fail_planning_move: bool` (levanta `ApplicationError(..., non_retryable=True)`). Registrar no `Worker`.

```python
async def test_happy_path(self) -> None:
    ...
    self.assertEqual(fake.calls, [
        "move_issue:In Planning", "create_sandbox", "clone_repo",
        "generate_plan", "commit_plan", "kill_sandbox", "move_issue:In Progress",
    ])
    self.assertEqual(fake.moves[0], MoveIssueRequest("issue-1", to_state="In Planning", from_state="To Do"))
    self.assertEqual(fake.moves[1], MoveIssueRequest("issue-1", to_state="In Progress"))

async def test_plans_even_when_planning_move_is_skipped(self) -> None:
    fake = FakeActivities(skip_planning_move=True)
    await self.run_workflow(fake)
    self.assertEqual(fake.calls[-1], "move_issue:In Progress")

async def test_planning_move_failure_creates_no_sandbox(self) -> None:
    fake = FakeActivities(fail_planning_move=True)
    with self.assertRaises(WorkflowFailureError):
        await self.run_workflow(fake)
    self.assertEqual(fake.calls, ["move_issue:In Planning"])
```

Nos testes de falha existentes (`test_kills_sandbox_on_failure`, `test_kills_sandbox_on_commit_failure`), acrescentar: `self.assertNotIn("move_issue:In Progress", fake.calls)`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONPATH=src python3 -m unittest tests.test_workflow -v`
Expected: FAIL — `calls` sem `move_issue:*`

- [ ] **Step 3: Implementar no `workflow.py`**

Importar `MoveIssueRequest` e as constantes dentro do `imports_passed_through()` já existente. Atualizar a docstring da classe.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests`
Expected: PASS

- [ ] **Step 5: Atualizar docs**

- `CLAUDE.md`, "Stack definida": nova linha **Linear API:** GraphQL (`https://api.linear.app/graphql`) via `httpx`, personal API key em `LINEAR_API_KEY` (worker). Remover "acesso à API do Linear" da lista de "Ainda não definidos".
- `CLAUDE.md`, "Estrutura": `linear/client.py` (cliente GraphQL) e `orchestration/linear_activities.py` (`LinearActivities.move_issue`); `models.py` cita `MoveIssueRequest`; descrição do `workflow.py` com a nova ordem.
- `CLAUDE.md`, "Webhook do Linear": o workflow move a issue `To Do → In Planning` no início (só se estiver em `To Do`) e `→ In Progress` depois do commit do plano; em falha fica em `In Planning`. Status procurados por nome no time da issue.
- `CLAUDE.md`, "Comandos": `LINEAR_API_KEY` na lista de envs do worker.
- `README.md`: linha `LINEAR_API_KEY | worker | personal API key do Linear, escrita em issues (muda o status)` na tabela de variáveis; mencionar os status `To Do`, `In Planning`, `In Progress` que precisam existir no time.

- [ ] **Step 6: Commit**

```bash
git add src/software_factory/orchestration/workflow.py tests/test_workflow.py CLAUDE.md README.md
git commit -m "feat: workflow move a issue para In Planning e In Progress"
```
