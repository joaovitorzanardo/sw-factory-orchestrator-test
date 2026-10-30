# Plano via writing-plans + commit do plano — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O `PlanIssueWorkflow` gera o plano com a skill `writing-plans` (superpowers) dentro do sandbox, salva em `docs/superpowers/plans/<IDENTIFIER>.md` e faz commit + push desse arquivo numa branch `factory/<IDENTIFIER>` do repo alvo.

**Architecture:** A skill é vendorizada neste repo e enviada ao sandbox em `~/.claude/skills/writing-plans/SKILL.md` antes de rodar o `claude -p`. O Claude ganha permissão de escrita apenas no arquivo do plano (`Edit(<caminho>)`); quem faz git é uma nova activity `commit_plan`, determinística e idempotente — o Claude não recebe `Bash`.

**Tech Stack:** Python 3.12, temporalio, e2b (`AsyncSandbox`), Claude Code CLI headless, unittest.

**Spec:** conversa de 2026-10-30 (resumo): usar a skill `superpowers:writing-plans` para gerar o plano; dar ao Claude permissão de escrita só para o plano; commitar o plano no repo antes da execução para rastrear o raciocínio; **não** commitar transcript (`.jsonl`), só o plano.

## Global Constraints

- Sem nova dependência Python; nada de instalar plugin via rede no sandbox (skill vendorizada, versão fixa: superpowers 7.0.0).
- Workflow continua determinístico; todo git/E2B fica em activities com timeout e retry explícitos.
- Activities idempotentes: rerun no mesmo sandbox não pode duplicar commit nem falhar no push.
- Caminho do plano no repo alvo: `docs/superpowers/plans/<IDENTIFIER>.md` (ex.: `docs/superpowers/plans/ENG-12.md`).
- Branch: `factory/<IDENTIFIER>` (ex.: `factory/ENG-12`).
- Autor do commit: `software-factory <software-factory@users.noreply.github.com>`; mensagem: `docs: plano de implementação <IDENTIFIER>`.
- Tokens nunca aparecem na string de comando (usar `envs`), como já é feito no `clone_repo`.
- `GITHUB_TOKEN` passa a exigir **Contents: Read and write** no `TARGET_REPO`.
- Textos de prompt, logs, docs e mensagens de commit em português, como o resto do código.

## Review Focus

1. Identifier fora do formato do Linear (ex.: `ENG-12; rm -rf /`) — vai para comando shell e caminho; deve ser rejeitado com `ApplicationError` não-retentável antes de qualquer comando. Teste em Task 1.
2. Retry do `generate_plan` com o plano de uma tentativa anterior já no disco — o `Write` do Claude exige `Read` prévio em arquivo existente; o arquivo antigo deve ser removido antes de rodar. Teste em Task 1.
3. Claude termina com `success` mas não salva o arquivo — deve falhar com `ApplicationError`, não devolver plano vazio. Teste em Task 1.
4. Retry do `commit_plan` depois de um push já feito — não pode criar segundo commit nem tentar push de novo. Teste em Task 2.
5. Branch `factory/<IDENTIFIER>` já existe no remoto com outro conteúdo (ex.: issue reprocessada) — falha não-retentável com mensagem clara, sem force push. Teste em Task 2.

---

### Task 1: Skill vendorizada e `generate_plan` lendo o plano do arquivo

**Files:**
- Create: `src/software_factory/orchestration/skills/writing-plans/SKILL.md` (cópia literal de `~/.claude/plugins/cache/claude-plugins-official/superpowers/7.0.0/skills/writing-plans/SKILL.md`)
- Create: `src/software_factory/orchestration/skills/writing-plans/LICENSE` (cópia do `LICENSE` MIT do plugin, mesma pasta 7.0.0)
- Modify: `pyproject.toml` (package-data)
- Modify: `src/software_factory/orchestration/activities.py`
- Test: `tests/test_activities.py`

**Interfaces:**
- Consumes: nada novo.
- Produces:
  - `PLANS_DIR = "docs/superpowers/plans"` (relativo ao repo)
  - `plan_path(identifier: str) -> str` → `"docs/superpowers/plans/ENG-12.md"`; levanta `ApplicationError(non_retryable=True)` se `identifier` não casar `^[A-Za-z0-9]+-[0-9]+$`.
  - `SandboxActivities.generate_plan(request: PlanRequest) -> str` (assinatura inalterada; agora retorna o conteúdo do arquivo do plano).

- [ ] **Step 1: Adaptar os fakes e escrever os testes que falham**

Em `tests/test_activities.py`:
- `FakeCommands` ganha `responses: dict[str, str]` (primeira chave contida em `cmd` define o `stdout`; senão usa `self.stdout`) e `on_run: Callable[[str], None] | None` chamado com `cmd` antes de retornar.
- `FakeFiles` ganha `read(path) -> str` (de `written`; `KeyError` vira `e2b.FileNotFoundException`), `remove(path)` (apaga de `written`/`existing` e registra em `removed: list[str]`), e `exists` passa a considerar também as chaves de `written`.

Testes (constante `PLAN_FILE = f"{REPO_DIR}/docs/superpowers/plans/ENG-12.md"`):

```python
def test_plan_path(self) -> None:
    self.assertEqual(plan_path("ENG-12"), "docs/superpowers/plans/ENG-12.md")

def test_plan_path_rejects_unsafe_identifier(self) -> None:
    for bad in ["ENG-12; rm -rf /", "../ENG-12", "ENG", ""]:
        with self.assertRaises(ApplicationError) as ctx:
            plan_path(bad)
        self.assertTrue(ctx.exception.non_retryable)

def test_prompt_uses_skill_and_path(self) -> None:
    prompt = build_plan_prompt(ISSUE)
    self.assertIn("writing-plans", prompt)
    self.assertIn("docs/superpowers/plans/ENG-12.md", prompt)
```

`test_generate_plan` reescrito: `on_run` grava `"# Plano\n1. fazer"` em `PLAN_FILE` quando `"claude -p"` está no comando; o result do stream é `"Plano salvo."`. Asserções:
- retorno == `"# Plano\n1. fazer"` (vem do arquivo, não do `result`);
- `written["/home/user/.claude/skills/writing-plans/SKILL.md"]` começa com `"---\nname: writing-plans"`;
- o comando contém `"Skill"` e `"Edit(docs/superpowers/plans/ENG-12.md)"` e **não** contém `"Bash"`;
- asserções antigas (cwd, sem `anthropic-key`, heartbeats) mantidas.

Novos:
- `test_generate_plan_removes_stale_plan`: `written[PLAN_FILE] = "antigo"` antes de rodar; `PLAN_FILE in files.removed` e o retorno é o novo conteúdo.
- `test_generate_plan_missing_file`: `on_run` não grava nada, result `success` → `ApplicationError`.
- `test_generate_plan_error_result` e `test_generate_plan_without_result` continuam passando sem mudança de asserção.

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONPATH=src python3 -m unittest tests.test_activities -v`
Expected: FAIL/ERROR (`plan_path` não existe; asserções do comando falham).

- [ ] **Step 3: Copiar a skill e o LICENSE; declarar package-data**

```toml
[tool.setuptools.package-data]
"software_factory.orchestration" = ["skills/*/*"]
```

- [ ] **Step 4: Implementar em `activities.py`**

- Constantes: `SKILL_SOURCE = Path(__file__).parent / "skills" / "writing-plans" / "SKILL.md"`, `SKILL_DEST = "/home/user/.claude/skills/writing-plans/SKILL.md"`, `PLANS_DIR`.
- `PLAN_COMMAND` vira `plan_command(identifier: str) -> str` com `--allowedTools "Read,Grep,Glob,Skill,Edit(<plan_path>)"` (regra `Edit` cobre também a ferramenta `Write`).
- `build_plan_prompt(issue)` passa a pedir, em português: usar a skill `writing-plans`; a issue é a spec (no campo **Spec:** do cabeçalho, `Issue <identifier> (<url>)`); salvar exatamente em `plan_path(issue.identifier)`, substituindo o caminho padrão da skill; não alterar nenhum outro arquivo; pular a seção "Execution Handoff" — sem perguntas, terminar quando o plano estiver salvo e revisado. Mantém título, URL e descrição (com o fallback `(sem descrição)`).
- `generate_plan`: calcula `plan_path` primeiro (valida identifier antes de tocar o sandbox); escreve `SKILL_DEST` com `SKILL_SOURCE.read_text()`; remove o arquivo do plano se `files.exists`; escreve o prompt; roda o comando (heartbeat igual hoje); `parse_plan(result.stdout)` só para detectar falha (valor descartado); lê `f"{REPO_DIR}/{path}"` — `FileNotFoundException` vira `ApplicationError("Claude Code não salvou o plano em <path>")`; retorna o conteúdo.

- [ ] **Step 5: Rodar e ver passar**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests`
Expected: OK (`test_workflow` ainda passa — nada mudou nele).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/software_factory/orchestration/skills src/software_factory/orchestration/activities.py tests/test_activities.py
git commit -m "feat: plano gerado com a skill writing-plans e salvo em arquivo"
```

---

### Task 2: Activity `commit_plan`

**Files:**
- Modify: `src/software_factory/orchestration/models.py`
- Modify: `src/software_factory/orchestration/activities.py`
- Test: `tests/test_activities.py`

**Interfaces:**
- Consumes: `plan_path(identifier)` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class CommitPlanRequest: sandbox_id: str; identifier: str` em `models.py`.
  - `plan_branch(identifier: str) -> str` → `"factory/ENG-12"` (valida via `plan_path`).
  - `SandboxActivities.commit_plan(request: CommitPlanRequest) -> str` — retorna o nome da branch.

- [ ] **Step 1: Escrever os testes que falham**

Comandos esperados (todos com `cwd=REPO_DIR`, timeout curto), nesta ordem:
1. `git checkout -B factory/ENG-12`
2. `git add docs/superpowers/plans/ENG-12.md`
3. `git diff --cached --quiet || git -c user.name="software-factory" -c user.email="software-factory@users.noreply.github.com" commit -m "docs: plano de implementação ENG-12"`
4. `git rev-parse HEAD` → SHA local
5. `git ls-remote --heads origin refs/heads/factory/ENG-12` → vazio ou `"<sha>\trefs/heads/factory/ENG-12"`
6. `git push -u origin factory/ENG-12` — só quando o remoto não tem a branch.

Testes (via `responses`: `"rev-parse"` → `"abc123\n"`, `"ls-remote"` → conforme o caso):
- `test_commit_plan_pushes_new_branch`: ls-remote vazio → retorno `"factory/ENG-12"`; os 6 comandos aparecem em ordem; nenhum contém `"gh-token"`; `PLAN_FILE` existe (pré-condição em `files.existing`).
- `test_commit_plan_idempotent_after_push`: ls-remote `"abc123\trefs/heads/factory/ENG-12\n"` → retorna a branch e nenhum comando contém `"git push"`.
- `test_commit_plan_rejects_diverged_remote`: ls-remote `"zzz999\trefs/heads/factory/ENG-12\n"` → `ApplicationError` com `non_retryable` e `"factory/ENG-12"` na mensagem; nenhum `"git push"`.
- `test_commit_plan_missing_plan_file`: plano ausente → `ApplicationError`, nenhum comando executado.

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONPATH=src python3 -m unittest tests.test_activities -v`
Expected: ERROR (`CommitPlanRequest`/`commit_plan` não existem).

- [ ] **Step 3: Implementar `CommitPlanRequest`, `plan_branch` e `commit_plan`**

Constantes `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL` com os valores das Global Constraints. O push usa o `origin` do clone (o token já está na URL do remoto gravada pelo `clone_repo`); não passar o token no comando. SHA remoto = primeiro campo da primeira linha do `ls-remote` (vazio → branch nova).

- [ ] **Step 4: Rodar e ver passar**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add src/software_factory/orchestration/models.py src/software_factory/orchestration/activities.py tests/test_activities.py
git commit -m "feat: activity commit_plan faz commit e push do plano"
```

---

### Task 3: Workflow e worker chamam `commit_plan`

**Files:**
- Modify: `src/software_factory/orchestration/models.py`
- Modify: `src/software_factory/orchestration/workflow.py`
- Modify: `src/software_factory/orchestration/worker.py`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `CommitPlanRequest`, activity `"commit_plan"` → `str` (Task 2).
- Produces: `PlanResult` ganha o campo `branch: str` (último campo, depois de `sandbox_id`).

- [ ] **Step 1: Escrever os testes que falham**

`FakeActivities` ganha `commit_plan(request: CommitPlanRequest) -> str` (`name="commit_plan"`, registra a chamada, retorna `f"factory/{request.identifier}"`, levanta `RuntimeError` se `fail_commit=True`); registrar no `Worker` do teste.

```python
# test_happy_path
self.assertEqual(
    result,
    PlanResult(identifier="ENG-12", plan="plano para ENG-12 em sbx-1",
               sandbox_id="sbx-1", branch="factory/ENG-12"),
)
self.assertEqual(
    fake.calls,
    ["create_sandbox", "clone_repo", "generate_plan", "commit_plan", "kill_sandbox"],
)
```

Novo `test_kills_sandbox_on_commit_failure`: `fail_commit=True` → `WorkflowFailureError`; `fake.calls[-1] == "kill_sandbox"`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONPATH=src python3 -m unittest tests.test_workflow -v`
Expected: FAIL (`PlanResult` sem `branch`; `commit_plan` não chamado).

- [ ] **Step 3: Implementar**

No `try` do workflow, depois do `generate_plan`: `execute_activity("commit_plan", CommitPlanRequest(sandbox_id, issue.identifier), start_to_close_timeout=timedelta(minutes=3), retry_policy=RetryPolicy(maximum_attempts=3), result_type=str)`. Retornar `PlanResult(..., branch=branch)`. Registrar `activities.commit_plan` no `worker.py`. Atualizar a docstring do workflow.

- [ ] **Step 4: Rodar e ver passar**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add src/software_factory/orchestration tests/test_workflow.py
git commit -m "feat: workflow commita o plano na branch factory/<issue>"
```

---

### Task 4: Documentação e smoke test real

**Files:**
- Modify: `CLAUDE.md` (Estrutura: `skills/`, `commit_plan`, fluxo do workflow; Webhook: o plano agora é commitado em `factory/<IDENTIFIER>`)
- Modify: `README.md` (tabela de env: `GITHUB_TOKEN` com **Contents: Read and write**; descrição do fluxo)
- Modify: `.env.example` (comentário do `GITHUB_TOKEN` com a permissão)

- [ ] **Step 1: Atualizar os três arquivos**

- [ ] **Step 2: Smoke test com sandbox real**

Com `temporal server start-dev`, `software-factory-worker` e um `TARGET_REPO` de teste, disparar o workflow (webhook ou `temporal workflow start --type PlanIssueWorkflow --task-queue <fila> --input '<PlanIssueInput JSON>'`).
Expected: workflow `Completed`; resultado com `branch: "factory/<ID>"`; no GitHub, a branch tem um único commit novo contendo só `docs/superpowers/plans/<ID>.md`, cujo conteúdo começa com `# ` e o header da skill (`**Goal:**`). Confirma também que a regra `Edit(...)` libera o `Write` no CLI headless — se o Claude reportar permissão negada, adicionar `Write(<plan_path>)` ao `--allowedTools` e o teste correspondente.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md .env.example
git commit -m "docs: plano commitado via writing-plans"
```
