# Runbook: passwords and persisted data volumes

ASAP's data volumes (`postgres-pv`, `keycloak-postgres-pv`, `neo4j-pv`) use
the *Retain* policy: `install-charts.sh` reinstalls every chart but the data
survives. That is the intended behavior — and it has one consequence that
has bitten us: **database passwords are initialised from the install flags
only when a volume is empty.** Passing a *different* password to a later
install updates the Kubernetes Secret but not the password the database
actually holds, and the two diverge silently. The three stores behave
differently.

| Store | Flag | Synced automatically? |
|---|---|---|
| ASAP PostgreSQL (role `asap`) | `--pg-password` | **Yes** — lifecycle hook on every start |
| Keycloak's PostgreSQL (role `keycloak`) | `--keycloak-admin-password` (reused as the DB password) | **Yes** — same hook |
| Keycloak **admin console** (user `admin`, master realm) | `--keycloak-admin-password` | **No** — set at first boot only |
| Neo4j (user `neo4j`) | `--neo4j-password` | **No** — set at first initialisation only |

## PostgreSQL (both instances) — automatic

Each Postgres StatefulSet carries a `postStart` hook that runs
`ALTER USER … WITH PASSWORD` over the local Unix socket (trust auth) using
the container's own `POSTGRES_PASSWORD`, so the role always matches the
Secret. It is fail-open (never blocks or kills the container) and logs
`[password-sync] …` to the container log:

```bash
kubectl logs -n asap postgres-0 | grep password-sync
kubectl logs -n asap keycloak-postgres-0 | grep password-sync
```

A `checksum/secret` pod annotation restarts the pod when the Secret changes,
so a plain `helm upgrade --set auth.password=…` re-runs the sync too.

## Keycloak admin console — manual, handle with care

`KC_BOOTSTRAP_ADMIN_PASSWORD` creates the `admin` user **only on Keycloak's
first start against an empty database**. Afterwards the console password
lives inside Keycloak's database and the flag has no effect on it. The
install script reuses one value for both the console and Keycloak's DB role,
so changing `--keycloak-admin-password` on an existing cluster yields:
DB role re-synced (hook) → Keycloak starts fine → **console still expects
the OLD password.**

- To rotate the console password: sign in with the current one and change
  it in the admin console (`admin` user → Credentials). Then pass the new
  value to future installs so the DB role follows.
- If the console password is lost: restore the most recent Keycloak DB
  backup, or use Keycloak 26's recovery command to create a temporary admin
  (verify the exact syntax for the deployed version before running):

  ```bash
  kubectl exec -n asap deployment/keycloak -- \
    /opt/keycloak/bin/kc.sh bootstrap-admin user --username tempadmin --password:env TEMP_PW
  ```

  then sign in as `tempadmin`, reset `admin`, and delete `tempadmin`.

Always back up first: `kubectl exec -n asap keycloak-postgres-0 -- pg_dump -U keycloak keycloak > keycloak-$(date +%F).sql`

## Neo4j — manual (needs the old password), or reset

Neo4j applies `NEO4J_AUTH` only when its data directory is first initialised;
changing `--neo4j-password` later leaves the `neo4j` user on the old password
while every client (backend, MCP, extractor, kggenerator) presents the new
one. Two paths:

1. **Rotate in place** (you know the old password):
   ```bash
   kubectl exec -n asap neo4j-0 -- cypher-shell -u neo4j -p '<OLD>' \
     "ALTER USER neo4j SET PASSWORD '<NEW>'"
   ```
2. **Reset the volume** (old password unknown). The graph is *derived*: it is
   rebuilt by extraction (~3 h) and the knowledge graph reloads in minutes
   from artifacts kept in Postgres. Stop the chart, delete `neo4j-pv`'s host
   directory contents, reinstall with the new password, run Extraction and
   then Generate Knowledge Graph from the admin panel. Document collections
   embedded in Neo4j must be re-uploaded.

## Symptoms that mean "password diverged"

- Backend log: `psycopg.pool error connecting … password authentication failed`
  → Postgres role out of sync (should now self-heal on restart).
- Keycloak pod CrashLoop with `FATAL: password authentication failed for user "keycloak"`
  → Keycloak DB role out of sync (self-heals on next keycloak-postgres start; restart Keycloak).
- Backend/MCP log: `Neo.ClientError.Security.Unauthorized`
  → Neo4j password diverged; follow the Neo4j section.
- `kubectl exec … psql` **still works** during all of the above (local trust
  auth) — do not take that as evidence the password is correct.

Removed charts (e.g. `ollama`, removed 2026-09-15) are not known to
`uninstall-charts.sh`; run `helm uninstall <name> -n asap` once on clusters
that predate the removal.
