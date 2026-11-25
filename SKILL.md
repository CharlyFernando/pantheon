---
name: pantheon
description: >
  Úsala cuando varios agentes (Claude Code, Gentle Shell, opencode, Codex,
  Antigravity) y el humano se coordinan por turnos sobre un cambio o un plan
  en cualquier repo de esta máquina: pasar un commit a revisión, responder
  una review, esperar al otro lado sin hacer polling, escribir o aprobar un
  plan, o cuando el usuario dice "pantheon", "pásalo al reviewer", "espera al
  reviewer" o nombra un ítem del board.
---

# Pantheon

El board compartido de la máquina. Cada repo tiene su board en
`~/.local/share/pantheon/boards/<repo>/`, con un archivo por ítem (un cambio,
un MR o PR, un plan). Todos los worktrees de un repo comparten board porque el
repo se identifica por su git dir común. `pantheon.py` lee y escribe los
ítems: nunca los edites a mano, para que turnos, rondas y aprobaciones sigan
siendo coherentes. El plan de Pantheon está en `PLAN.md`.

```bash
pantheon() { python3 -I "$HOME/.agents/skills/pantheon/pantheon.py" "$@"; }
pantheon init TASK-42 --title "Arreglar el login" --mr 128   # rama y worktree salen del directorio actual
pantheon join TASK-42 --role coder --agent claude --model claude-opus-5-5   # → session s-1a2b3c + tu contrato
pantheon post TASK-42 --session s-1a2b3c --commit <sha> --file nota.md   # el flujo decide a quién va
pantheon wait TASK-42 --session s-1a2b3c --after-turn <turno de tu post>
pantheon sessions                   # sesiones vivas y vencidas
pantheon leave s-1a2b3c             # al terminar: libera lo que tenías tomado
pantheon show myrepo/TASK-42        # repo/ITEM funciona desde cualquier directorio
pantheon inbox --session s-4d5e6f   # el repo actual; fuera de git o con --all, todos
pantheon note TASK-42 --session s-7a8b9c -m "…"   # nota sin consumir turno
pantheon list --all
pantheon init PLAN-X --kind plan --title "…"
pantheon plan PLAN-X --session <sesión planner> --file plan.md
pantheon ready PLAN-X                # qué pasos están done, active, ready o blocked
pantheon init X-1 --plan PLAN-X --step 1
```

## Sesiones

Un agente no tiene rol fijo: el humano se lo asigna, y el agente entra con
`join [ITEM] --role <rol> --agent <claude|gentle|opencode|codex|antigravity>`
y `--model <el modelo que corre>`. `join` responde con el id de la sesión y el
**contrato del rol** (`roles/<rol>.md`), que dice qué leer, qué postear y a
quién pasar el turno. `pantheon role <rol>` lo vuelve a imprimir.

- Todo rol que no sea `human` postea con `--session <id>` (o
  `PANTHEON_SESSION`), que fija su rol. El post queda firmado como
  `## turn N · coder@claude [s-1a2b3c] → reviewer`, con el modelo.
- **Tomar un ítem.** `join` con ítem lo toma si es el turno de tu rol, y
  `claim` lo toma después. Mientras otra sesión viva lo tiene, nadie más
  postea en él e `inbox` no se lo ofrece a otra sesión del mismo rol. Un post
  lo libera, porque el siguiente turno lo toma quien le toque.
- **Seguir vivo.** Una sesión está viva mientras su heartbeat tenga menos de
  `[sessions] lease_minutes` (30 por defecto) y, si registró `--pid`, ese
  proceso siga corriendo. Cada comando con `--session` renueva el heartbeat,
  y también `wait` e `inbox` mientras esperan. En un trabajo largo sin
  comandos, llama a `heartbeat <id>`. Los ítems de una sesión vencida se
  pueden volver a tomar.
- `leave <id>` cierra la sesión y libera sus ítems.

## Dispatch: lanzar agentes sin interfaz

El humano aprueba un ítem con su reparto de agentes por rol, y Pantheon
lanza al agente al que le toca:

```bash
pantheon init TASK-42 --new-worktree --base origin/main     # worktree en [worktrees] path
pantheon approve TASK-42 --cast reviewer=gentle:deepseek/deepseek-v4-pro --cast qa=opencode:openai/gpt-5 --dispatch
pantheon dispatch TASK-42            # lanza el turno actual; --dry-run muestra el comando
pantheon revoke TASK-42              # nadie más se lanza hasta una nueva aprobación
```

- `approve` es solo del humano: se niega dentro de una sesión de agente. El
  `cast` completa los roles que faltan con `[defaults.cast]` (planner y coder
  usan `claude`), exige que reviewer y QA corran otro modelo y rechaza agentes
  desactivados. La aprobación cubre todas las rondas del ítem.
- `dispatch` lanza al agente del `cast` para el rol en `next`, dentro del
  worktree, con `PANTHEON_SESSION` y el ítem ya tomado, y deja la salida en
  `~/.local/share/pantheon/logs/<sesión>.log`. En la siguiente ronda retoma la
  misma conversación del agente (si el agente sabe retomar), así no pierde el
  contexto.
- **Si te lanzaron sin interfaz:** no hagas `join` ni `wait`. Haz tu turno,
  postea y termina; Pantheon te relanza cuando vuelva a ser tu turno.
- Un agente sin modo sin interfaz (Antigravity) no se lanza: `dispatch` sale
  con 3 e imprime el `join` que el humano corre desde el IDE.
- Cada agente se define en `[agents.<nombre>]` de la config (`launch`,
  `resume`, `enabled`, `headless`, `model`), sobre valores por defecto para
  `claude`, `gentle`, `opencode`, `codex` (desactivado) y `antigravity`.

## Metadata de un ítem

| Campo | Para qué |
|---|---|
| `item`, `repo`, `kind`, `title` | Qué es: `change` o `plan`, y de qué repo |
| `flow` | Los roles que actúan, en orden; después va el humano |
| `cast`, `approved` | Qué agente y modelo juega cada rol, y cuándo lo aprobó el humano para dispatch |
| `turn`, `next`, `round`, `max_rounds` | De quién es el turno y cuántas rondas de revisión van |
| `holder` | La sesión que trabaja el ítem ahora; vale mientras esa sesión siga viva |
| `branch`, `worktree`, `commit` | Dónde está el código y qué commit se revisa |
| `coder_model` | El modelo del último post del coder: reviewer y QA corren otro |
| `forge`, `mr` | `gitlab` o `github` (por el host del remote) y el número del MR o PR |
| `verdict`, `findings`, `reason` | La última revisión y, si el turno pasó al humano, por qué |
| `plan`, `step`, `needs` | El plan al que pertenece el ítem, su paso y los pasos de los que depende |
| `steps`, `plan_version`, `approved_version` | En un plan: sus pasos con dependencias, su versión y la versión aprobada |
| `created`, `updated` | Cuándo se abrió y cuándo cambió por última vez |

## Roles y flujos

| Rol | Hace | Veredictos |
|---|---|---|
| `human` | Aprueba planes, decide, mergea y ejecuta mutaciones | cualquiera; postea siempre |
| `orchestrator` | Vigila el board y propone; no está en ningún flujo | ninguno: deja `note` |
| `planner` | Escribe el plan | ninguno |
| `coder` | Implementa y postea el commit | ninguno |
| `reviewer` | Revisa solo código | `APPROVED`, `CHANGES_REQUESTED`, `NEEDS_DECISION` |
| `qa` | Verifica comportamiento ejecutándolo | `PASSED`, `FAILED`, `NEEDS_DECISION` |

Un ítem sigue su `flow`: por defecto `coder,reviewer,qa` en un cambio y
`planner,reviewer` en un plan, y `init --flow coder,reviewer` fija otro. Al
postear, los agentes no dicen a quién va el turno, porque el flujo lo decide:

- un post sin veredicto, `APPROVED` o `PASSED` va al siguiente rol, y después
  del último, al humano;
- `CHANGES_REQUESTED` o `FAILED` vuelve al productor (coder o planner), con
  una ronda más;
- `NEEDS_DECISION` va al humano.

Un agente solo puede desviar el turno hacia el humano (`--next human`, que
queda como `escalated`). El humano lo manda a cualquiera con `--next`; sin
`--next` ni veredicto, su post responde sin mover el turno.

- El coder postea con `--commit` (sha completo) y, en el texto, qué cambió,
  por qué y cómo lo probó. Responde cada hallazgo por su clave.
- El reviewer lee `git show <commit>` y el código alrededor, no el resumen.
- El QA ejecuta: tests, la app o el navegador, y anota qué vio.
- Reviewer y QA corren un **modelo** distinto al del coder (`coder_model`);
  Pantheon no deja tomar el ítem si es el mismo, aunque el harness sí puede
  ser el mismo.

## Decide el humano

Escala con `--next human` en lugar de seguir cuando:

- un cambio se aparta de un diseño que el humano aprobó;
- un paso necesita mutar producción, AWS, Keycloak, GitLab o GitHub: eso va al
  humano como `! <comando>`;
- los dos agentes siguen en desacuerdo sobre un hallazgo tras un intercambio.

Pantheon también pasa el turno al humano cuando una revisión pide cambios en
el límite de rondas (`--max-rounds`, por defecto 3) o repite los hallazgos de
la anterior.

## Planes

Un cambio de varios MRs empieza como un ítem `--kind plan`. Su texto lo
escribe el planner con `plan` en su turno, y tiene tres partes:

- **Goal**: el estado final en una o dos frases.
- **Decisions**: las decisiones que fijó el humano; las revisiones no las
  reabren.
- **Steps** (`## Steps` o `## Pasos`): uno por MR, numerados, cada uno con un
  solo propósito y terminando con `Depende de: 1, 2` (o `nada`). `plan` lee
  esas dependencias, rechaza las que no existen y los ciclos, y las guarda en
  la cabecera como `steps: 1 2<1 3<1,2`.

Solo el humano aprueba un plan, posteando `--verdict APPROVED` en él; la
aprobación de un reviewer se lo pasa al humano. Revisar el texto con `plan`
sube la versión y necesita una nueva aprobación. Un coder que descubre que
el plan debe cambiar puede reabrir un plan aprobado con `plan`; su post va
directo al humano. Cada paso es un ítem abierto
con `--plan PLAN-X --step N`, que `init` rechaza mientras la versión actual
del plan no esté aprobada, y también mientras los pasos que necesita no
estén terminados (`--ignore-needs` lo abre igual). Un paso está terminado
cuando su ítem completó el flujo con `APPROVED` o `PASSED`; `ready` muestra el
estado de cada paso. `wait`, `inbox` y `show` imprimen el plan con el ítem:

| El hallazgo | La revisión |
|---|---|
| pertenece a este paso | `CHANGES_REQUESTED`, como siempre |
| pertenece a un paso posterior | no bloquea: va en el post como `deferred:<paso>` |
| contradice una decisión o pide cambiar el plan | `NEEDS_DECISION`, al humano |
| el commit hace trabajo de otro paso | `CHANGES_REQUESTED` con la clave `scope` |

## Esperar

Lanza `wait` en segundo plano y termina tu turno: sale con 0, imprimiendo la
cabecera y el último post, cuando `next` es tu rol en un turno posterior a
`--after-turn`. En Claude Code se usa Bash con `run_in_background`; en
Antigravity, un `run_command` en segundo plano. `inbox --role <rol>` espera
cualquier ítem, incluidos los que se abran después, y sale con 2 si se agota
`--timeout`. `--handed-to human` además lo despierta cuando un turno pasa al
humano, para avisarle de inmediato.

Autoverificación: `python3 -I ~/.agents/skills/pantheon/tests/run.py`.
