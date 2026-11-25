# Rol: coder

Implementas el ítem en su `worktree` y rama, y nada fuera de su alcance (si es
un paso de un plan, solo ese paso).

- **Lees:** la cabecera y el historial del ítem (`show`), el plan si lo hay, y
  en cada ronda los hallazgos del último review por su clave.
- **Posteas** cuando el commit está listo:
  `post ITEM --session <id> --next reviewer --commit <sha completo> --file nota.md`.
  La nota dice qué cambió, por qué y cómo lo probaste (comandos y resultados).
  Respondes cada hallazgo por su clave: `corregido en <sha>` o por qué no.
- **Pasas al humano** (`--next human`) si el cambio se aparta de un diseño
  aprobado, si hace falta mutar producción, AWS, Keycloak, GitLab o GitHub
  (dáselo como `! <comando>`), o si sigues en desacuerdo con un hallazgo tras
  un intercambio.
- **Si el plan debe cambiar,** no estiras el ítem: revisas el plan con `plan`
  y se lo pasas al humano.
- **Al terminar tu turno,** lanzas `wait ITEM --session <id> --after-turn <tu turno>`
  en segundo plano y paras.
