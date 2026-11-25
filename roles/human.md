# Rol: human

Decides. Puedes postear en cualquier turno para responder, redirigir o
detener, y eres el único que:

- aprueba un plan (`--verdict APPROVED` sobre el plan);
- mergea un MR o PR y ejecuta mutaciones de producción, AWS, Keycloak, GitLab
  o GitHub que los agentes te pasen como `! <comando>`;
- resuelve un `NEEDS_DECISION`, un desacuerdo o el límite de rondas;
- asigna el rol de cada sesión al unir un agente.

Tu inbox: `inbox --role human --all`.
