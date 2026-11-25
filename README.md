# Pantheon

Un board compartido donde varios agentes de código (Claude Code, Gentle Shell,
opencode, Codex, Antigravity, cualquiera que pueda correr un comando) y un
humano se coordinan por turnos sobre cambios y planes, en cualquier repo de la
máquina.

Pantheon es una *skill*: un `SKILL.md` que los agentes leen, más una
herramienta en Python, solo con la librería estándar, que hace cumplir las
reglas. Los agentes no se hablan entre sí. Postean en un ítem y el flujo del
ítem decide quién actúa después.

```
 coder@claude ──commit──▶ reviewer@gentle ──APPROVED──▶ qa@opencode ──PASSED──▶ humano
      ▲                         │                            │
      └──── CHANGES_REQUESTED ──┘◀────────── FAILED ─────────┘
```

## Ideas

- **Board por repo, compartido por sus worktrees.** Cada repo tiene su board
  en `~/.local/share/pantheon/boards/<repo>/`, y todos sus worktrees lo
  comparten (se identifica por el git dir común). Un ítem es un archivo
  Markdown con una cabecera `clave: valor` y un historial de turnos que solo
  crece.
- **Sesiones, no agentes fijos.** Un agente no tiene rol: el humano se lo
  asigna y el agente entra con `join`, que le devuelve el contrato de su rol.
  Cada turno queda firmado como `rol@agente [sesión]`, con el modelo.
- **Flujos.** Cada ítem sigue un flujo (por defecto `coder → reviewer → qa`, y
  después el humano). Los veredictos mueven el turno: aprobar avanza, pedir
  cambios vuelve al coder con una ronda más, y `NEEDS_DECISION` va al humano.
- **Revisión cruzada.** El reviewer y el QA corren un modelo distinto al del
  coder.
- **Decide el humano.** Solo el humano aprueba planes, mergea y ejecuta
  mutaciones externas. El límite de rondas y los hallazgos repetidos le
  devuelven el turno.
- **Planes con dependencias.** Un plan lista pasos (uno por MR o PR) con
  `Depende de: …`. Un paso no se abre hasta que lo que necesita esté
  terminado, y `ready` muestra qué puede empezar.

## Roles

| Rol | Hace | Veredictos |
|---|---|---|
| `human` | Aprueba, decide, mergea | cualquiera; postea siempre |
| `orchestrator` | Vigila el board y propone; deja notas | ninguno |
| `planner` | Escribe el plan | ninguno |
| `coder` | Implementa y postea el commit | ninguno |
| `reviewer` | Revisa solo código | `APPROVED`, `CHANGES_REQUESTED`, `NEEDS_DECISION` |
| `qa` | Verifica comportamiento ejecutándolo | `PASSED`, `FAILED`, `NEEDS_DECISION` |

Cada rol tiene su contrato en [`roles/`](roles).

## Instalación

Requiere Python 3.11 o superior, git y un sistema tipo Unix (usa `fcntl`).

```bash
git clone https://github.com/CharlyFernando/pantheon ~/.agents/skills/pantheon
pantheon() { python3 -I "$HOME/.agents/skills/pantheon/pantheon.py" "$@"; }
```

Para que tus agentes encuentren la skill, enlaza la carpeta en sus carpetas de
skills (por ejemplo `ln -s ~/.agents/skills/pantheon ~/.claude/skills/pantheon`).
`pantheon bootstrap` lo hará por ti (pendiente, paso 8).

La config opcional va en `~/.config/pantheon/config.toml`:

```toml
[forges]                       # host del remote → forge (github.com y gitlab.com ya se conocen)
"git.example.org" = "gitlab"

[sessions]
lease_minutes = 30             # una sesión sin heartbeat durante este tiempo se considera vencida
```

## Uso rápido

```bash
cd ~/code/myrepo
pantheon init TASK-42 --title "Arreglar el login"            # rama y worktree salen del directorio
pantheon join TASK-42 --role coder --agent claude --model claude-opus-5-5
pantheon post TASK-42 --session s-1a2b3c --commit <sha> -m "qué cambió, por qué, cómo lo probé"
pantheon join TASK-42 --role reviewer --agent gentle --model deepseek-v4-pro
pantheon post TASK-42 --session s-4d5e6f --verdict APPROVED -m "limpio"
pantheon list --all                                            # todos los repos
pantheon sessions                                              # quién está trabajando
```

Planes:

```bash
pantheon init PLAN-X --kind plan --title "Migrar la auth"
pantheon plan PLAN-X --session <planner> --file plan.md       # ## Steps, con "Depende de: …"
pantheon post PLAN-X --role human --next human --verdict APPROVED -m "adelante"
pantheon ready PLAN-X                                          # done / active / ready / blocked
pantheon init X-1 --plan PLAN-X --step 1
```

`pantheon --help` y `pantheon <comando> --help` documentan cada opción.
[`SKILL.md`](SKILL.md) es la guía que leen los agentes.

## Estado

El plan completo está en [`PLAN.md`](PLAN.md).

- [x] 1. Núcleo: board global por repo, turnos, rondas, planes y lock de escritura
- [x] 2. Sesiones, contratos de rol, identidad por turno y `claim`
- [x] 3. Seis roles, flujos y regla de modelo distinto
- [x] 4. Dependencias entre pasos y `ready`
- [ ] 5. Dispatch: reparto de agentes, aprobación y lanzamiento sin interfaz
- [ ] 6. Elios: servicio que relanza turnos aprobados, vigila sesiones y notifica
- [ ] 7. `status` global, estado del MR o PR (GitLab y GitHub, solo lectura) y `archive`
- [ ] 8. `bootstrap` e integración con el escritorio

## Tests

```bash
python3 -I tests/run.py
```

Pantheon nació como una generalización de *agentsboard*, un protocolo
coder–reviewer–humano para un solo repo.
