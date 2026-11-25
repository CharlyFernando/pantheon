# PLAN-PANTHEON

## Goal

Pantheon es el cerebro compartido de la máquina: un board global, por repo,
donde cualquier agente (Claude Code, Gentle Shell, opencode, Codex,
Antigravity) entra a una sesión con el rol que el humano le asigna y se
coordina por turnos con los demás. Elios, un servicio en Python, hace la parte
mecánica: relanza los turnos aprobados, vigila los leases y avisa al humano.

## Decisions

1. **Ubicación.** La skill vive en `~/.agents/skills/pantheon/` (repo git
   propio) y se enlaza en las carpetas de skills de cada harness. Los datos van
   en `~/.local/share/pantheon/boards/<repo>/` (`PANTHEON_HOME` lo cambia) y
   la config en `~/.config/pantheon/config.toml`. No se migra agentsboard:
   los repos que ya usan agentsboard conservan el suyo.
2. **Repo.** Un repo se identifica por su git dir común, así que todos sus
   worktrees (incluidos los de `~/.codex` y opencode) comparten el board. El
   slug es el nombre del repo; `--repo` lo fija a mano.
3. **Python de la librería estándar.** Toda la automatización y las tools
   internas son Python ≥ 3.11 sin dependencias: config en TOML (`tomllib`),
   sesiones en JSON, ítems en Markdown con cabecera `clave: valor`. Todo se
   escribe de forma atómica y bajo un lock por repo.
4. **Metadata mínima y útil.** Cada campo de la cabecera lo lee alguien
   (agente, Elios, `status`). No se añade un campo que nadie consulte.
5. **Agentes.** `claude`, `gentle` (Gentle Shell / pi), `opencode` y `codex`
   corren sin interfaz; `codex` empieza con `enabled = false` (sin saldo).
   `antigravity` es solo interactivo: el humano se une desde el IDE y Elios le
   avisa cuando le toca.
6. **Roles.** Son `human`, `orchestrator`, `planner`, `coder`, `reviewer` y
   `qa`. Un agente no tiene rol fijo: lo toma al entrar a una sesión, asignado
   por el humano. Solo `planner` y `coder` tienen agente por defecto, `claude`;
   los demás roles se asignan siempre en el `cast`.
7. **Reviewer y QA.** El reviewer solo revisa código (el diff y su contexto).
   El QA verifica el comportamiento contra los criterios del paso. Ambos usan
   un **modelo** distinto al del coder (el harness puede ser el mismo).
8. **Flujo.** Por defecto `coder → reviewer → qa → human`; cada ítem puede
   fijar el suyo con `--flow`.
9. **Aprobación.** El humano aprueba el plan, y además cada ítem con su
   `cast`; esa aprobación cubre todas las rondas del ítem, así que Elios
   relanza al coder para corregir sin preguntar. Cada paso nuevo necesita su
   propia aprobación. Nunca se lanza un agente sin `approved`.
10. **Mutaciones externas.** Producción, AWS, Keycloak y las escrituras en
    GitLab o GitHub van al humano como `! comando`. Pantheon solo lee del forge
    (`glab`, `gh`), detectado por el host del remote (tabla `[forges]`).
11. **Notificaciones.** `notify-send`, que muestra el shell de Caelestia. Los
    cambios de escritorio van en los archivos de usuario
    (`~/.config/caelestia/hypr-user.*`), nunca en el clon `~/caelestia`.

## Steps

1. **Núcleo.** Board global por repo: ítems, turnos, rondas y planes de
   agentsboard generalizados, con la metadata nueva (`repo`, `title`, `forge`,
   `created`, `updated`), referencias `repo/ITEM`, `list` e `inbox` de todos
   los repos, y lock de escritura. Depende de: nada.
2. **Sesiones y roles.** `join`/`leave`/`heartbeat`, registro en
   `sessions/<id>.json`, contratos en `roles/<rol>.md`, identidad por turno
   (`rol@agente [sesión]`) y `claim`. El lease es el heartbeat de la
   sesión: el ítem solo guarda `holder`, válido mientras la sesión viva.
   Depende de: 1.
3. **Flujos.** Los seis roles, el rol QA con `PASSED`/`FAILED`, `--flow` por
   ítem y la regla de modelo distinto. Depende de: 2.
4. **Dependencias del plan.** `needs` por paso y `ready`, que lista lo que
   puede empezar. Depende de: 1.
5. **Dispatch.** `cast` y `approve` por ítem, creación del worktree según la
   convención, y lanzamiento o reanudación del agente sin interfaz a partir de
   `config.toml`. Depende de: 2, 3 y 4.
6. **Elios.** Servicio de usuario de systemd: relanzar turnos aprobados, leases
   vencidos, desbloqueo de pasos y notificaciones. Depende de: 5.
7. **Status, forge y archive.** `status` global, estado del MR/PR con
   `glab`/`gh` (solo lectura) y `archive` con sugerencia de worktrees a
   borrar. Depende de: 1.
8. **Bootstrap y escritorio.** `pantheon bootstrap` (enlaces de la skill,
   comando en `~/.local/bin`, línea en los AGENTS/CLAUDE/GEMINI globales) y
   atajo de Hyprland que abre `status`. Depende de: 6 y 7.
