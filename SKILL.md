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
pantheon post TASK-42 --role coder --next reviewer --commit <sha> --file nota.md
pantheon wait TASK-42 --role coder --after-turn <turno de tu post>
pantheon show myrepo/TASK-42        # repo/ITEM funciona desde cualquier directorio
pantheon inbox --role reviewer      # el repo actual; fuera de git o con --all, todos
pantheon list --all
pantheon init PLAN-X --kind plan --title "…"
pantheon plan PLAN-X --role coder --file plan.md
pantheon init X-1 --plan PLAN-X --step 1
```

## Metadata de un ítem

| Campo | Para qué |
|---|---|
| `item`, `repo`, `kind`, `title` | Qué es: `change` o `plan`, y de qué repo |
| `turn`, `next`, `round`, `max_rounds` | De quién es el turno y cuántas rondas de revisión van |
| `branch`, `worktree`, `commit` | Dónde está el código y qué commit se revisa |
| `forge`, `mr` | `gitlab` o `github` (por el host del remote) y el número del MR o PR |
| `verdict`, `findings`, `reason` | La última revisión y, si el turno pasó al humano, por qué |
| `plan`, `step`, `plan_version`, `approved_version` | El plan al que pertenece el ítem o, en un plan, su versión aprobada |
| `created`, `updated` | Cuándo se abrió y cuándo cambió por última vez |

## Turnos

- `next` nombra quién actúa, y solo ese rol puede postear. El humano siempre
  puede, para responder, redirigir o detener.
- El coder postea cuando el commit está listo: `--commit` con el sha completo
  y, en el texto, qué cambió, por qué y cómo lo probó (comandos y resultados).
- El reviewer lee `git show <commit>` y el código alrededor, no el resumen del
  coder. Postea `--verdict APPROVED` (next `human`, que mergea),
  `CHANGES_REQUESTED` con `--findings KEY,KEY` (claves cortas y estables, una
  por hallazgo; next `coder`) o `NEEDS_DECISION` (next `human`).
- El coder responde cada hallazgo por su clave: corregido en `<sha>`, o por
  qué no.

## Decide el humano

Postea con `--next human` en lugar de seguir cuando:

- un cambio se aparta de un diseño que el humano aprobó;
- un paso necesita mutar producción, AWS, Keycloak, GitLab o GitHub: eso va al
  humano como `! <comando>`;
- los dos agentes siguen en desacuerdo sobre un hallazgo tras un intercambio.

Pantheon también pasa el turno al humano cuando una revisión pide cambios en
el límite de rondas (`--max-rounds`, por defecto 3) o repite los hallazgos de
la anterior.

## Planes

Un cambio de varios MRs empieza como un ítem `--kind plan`, cuyo texto escribe
`plan` y que tiene tres partes:

- **Goal**: el estado final en una o dos frases.
- **Decisions**: las decisiones que fijó el humano; las revisiones no las
  reabren.
- **Steps**: uno por MR, numerados, cada uno con un solo propósito y en orden
  de dependencias.

Solo el humano aprueba un plan, posteando `--verdict APPROVED` en él; la
aprobación de un reviewer se lo pasa al humano. Revisar el texto con `plan`
sube la versión y necesita una nueva aprobación. Cada paso es un ítem abierto
con `--plan PLAN-X --step N`, que `init` rechaza mientras la versión actual
del plan no esté aprobada. `wait`, `inbox` y `show` imprimen el plan con el
ítem:

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
