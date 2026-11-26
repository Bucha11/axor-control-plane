# Implementation Plan — «Запустить» проверку навыка из интерфейса

## Sources read
- UI: `frontend/src/tabs/SkillEvals.tsx` (`HowToRun` собирает shell-команду с heredoc; `EvalList`, `EvalView` без опроса)
- backend: `routers/skill_evals_api.py` (`POST/GET /v1/skill-evals`, scope ingest, таблица `skill_evals` 0022), `storage.py` (`developer_identities`, `node_profiles`), `auth.py` (скоупы), `migrations/versions` (голова 0039)
- axor-eval: `axor_eval/skills/{spec,runner,__main__}.py` — `run_eval(spec, on_trial=…)` зовёт колбэк после каждого прогона; прогон = копия репо + `claude -p … --permission-mode bypassPermissions` + shell-команда `check`; `post_to_plane` шлёт отчёт целиком в конце
- axor-wrap: `cli.py` (подкоманды), `connection.py` (`~/.axor/connection.json`: url + личный ключ), `agents/sidecar.py` (`developer_id`); постоянно работающего процесса на машине нет
- axor-lab `lab_server/runtime_jobs.py`: только как образец формы протокола (connect → poll/claim/progress/complete); сам механизм не переиспользуется — в памяти, без аренды, без исполнителя

## Key decisions
- **Исполнитель — новая команда `axor-wrap worker`**, которую разработчик сам запускает в своём репозитории (один раз, держит открытой). Только она забирает задачи. Никаких фоновых служб, которые ставятся сами.
- **Безопасность: машина выполняет только задачи своего владельца.** Задача — это запуск агента с `bypassPermissions` и произвольная shell-команда «done when», то есть выполнение кода на машине. Поэтому worker берёт задачу, только если её создал тот же человек, чей личный ключ у машины (email задачи = владелец ключа); плейн отдаёт машине только такие задачи. Админ не может поставить задачу на чужую машину. Worker печатает в терминал каждую задачу (навык, задачу, команду проверки) перед запуском.
- **Очередь — в базе control-plane**, с арендой: `queued → running → done | failed | cancelled | lost`. Захват атомарный (`UPDATE … WHERE state='queued'`). Worker продлевает аренду при каждом отчёте о прогресе; истёкшая аренда → `lost`, без автоповтора (прогоны стоят токенов пользователя).
- **Машина = (личный ключ, хост, репозиторий)**, видна в UI, пока присылает пульс (`last_seen`), с перечнем найденных агентов (`claude`, `codex` в PATH).
- **Прогресс:** колбэк `on_trial` из axor-eval → `POST …/progress` (сделано N из M, решено сейчас). UI опрашивает задачу раз в 3 с, пока она идёт. Отмена — между прогонами (внутри прогона axor-eval прервать не умеет).
- **Результат** — существующим `POST /v1/skill-evals`; задача получает `eval_id`, UI открывает отчёт.
- **Одна команда всё же остаётся:** `axor-wrap worker` при первом запуске (если ни одна машина не на связи, UI так и пишет). Но это один раз на машину, а не на каждую проверку.
- `axor-eval` — необязательная зависимость: `axor-wrap[eval]`.

## Steps

### Step 1 — backend: таблицы машин и задач
**Files:** `migrations/versions/0040_machine_jobs.py`, `axor_backend/storage.py`
**What:** `machines (org, machine_id, owner, host, repo_label, agents, last_seen)` и `machine_jobs (org, job_id, machine_id, created_by, kind, spec, state, lease_until, progress, eval_id, error, ts)`.
**Done when:** миграция вверх/вниз на SQLite проходит; тест атомарного захвата (второй захват той же задачи → отказ).

### Step 2 — backend: API машины (ingest-ключ)
**Files:** `routers/machines_api.py`, `auth.py`, `tests/test_machine_jobs.py`
**What:** `POST /v1/machines/heartbeat`, `GET /v1/machines/{id}/jobs` (только задачи владельца ключа), `POST /v1/machine-jobs/{id}/claim|progress|complete`.
**Done when:** тесты: чужая задача машине не видна и не захватывается; прогресс продлевает аренду; complete связывает `eval_id`.

### Step 3 — backend: API интерфейса (operate)
**Files:** `routers/machines_api.py`, `tests/test_machine_jobs.py`
**What:** `GET /v1/machines` (на связи / нет), `POST /v1/machine-jobs` (skill eval на свою машину; на чужую → 403), `GET /v1/machine-jobs[/{id}]`, `POST …/cancel`; уход аренды → `lost`.
**Done when:** тесты: задача на чужую машину отклонена; истёкшая аренда → `lost`; отмена видна машине.

### Step 4 — axor-wrap: `axor-wrap worker`
**Files:** `axor_wrap/worker.py`, `axor_wrap/cli.py`, `tests/test_worker.py`
**What:** пульс, опрос, захват, печать задачи, `run_eval(on_trial=…)` → progress, отчёт → `/v1/skill-evals`, complete; отмена между прогонами; Ctrl-C завершает текущую задачу как failed.
**Done when:** тест против фейкового плейна: задача проходит путь queued → running → done, прогресс приходит после каждого прогона; без `axor-eval` команда говорит, что поставить.

### Step 5 — frontend: «Запустить»
**Files:** `frontend/src/tabs/SkillEvals.tsx`, `frontend/src/api.ts`, `frontend/e2e/skill-evals-run.spec.ts`
**What:** выбор машины (на связи), поля формы, кнопка «Запустить», список задач с прогрессом и отменой; без машины — одна команда `axor-wrap worker`; готовая задача ведёт в отчёт.
**Done when:** e2e: машина на связи → запуск → прогресс → отчёт; без машины видна команда.

### Step 6 — релизы
**What:** axor-wrap (PyPI, extra `eval`), control-plane; пин и CHANGELOG.
**Done when:** worker из PyPI выполняет задачу с прода на твоей машине.

## Out of scope
- Задачи других видов (setup health и пр.) — тот же механизм, потом.
- Запуск на машинах вендора / в облаке.
- Прерывание прогона посередине.
