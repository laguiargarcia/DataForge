---
topic: Message task
read_when: When configuring an alert, notification, or any `type: message` task
subsystem: tasks
last_synced_at: "2026-05-28T07:00:00Z"
related_issues: [df-012, df-8or]
---

# Message Task (`type: message`)

Built-in task type that sends a templated message through one or more
channels in parallel. Registered automatically by `dataforge` at package
import via Spec A's task type registry.

## YAML schema

```yaml
- name: notify_on_failure
  type: message
  match: any
  depends_on:
    - {task: landing, when: failed}
  params:
    channels: [email, whatsapp]          # one or more registered channels
    recipients:                          # per-channel address map (B-F7)
      email: ops@example.com
      whatsapp: "+5511999999999"
    subject: "FAILED {run_id} on {workspace}"
    body: |
      Failed tasks: {failed_tasks}
      Started at: {started_at}
      Duration: {duration_seconds}s
```

### Recipients are per-channel (B-F7)

Earlier spec drafts used `recipient:` (a single string). The
implementation uses `recipients:` — a map keyed by channel name — because
the address shape differs between channels (email addresses vs phone
numbers). The spec example has been amended; older `recipient:` fields
are **not** accepted.

## Channels (built-in)

| Channel    | Env vars required                                                           |
|------------|------------------------------------------------------------------------------|
| `email`    | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`             |
| `whatsapp` | `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_WHATSAPP_FROM`                        |
| `sms`      | `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_SMS_FROM`                             |

Env vars resolve from `job.env_file` (workspace-relative path),
exactly the same loader as the `python` task type. Vars not listed in the
env file are **not** silently pulled from the OS — keep secrets in
`.env`, never inlined in `job.yaml`.

### SMTP transport (B-F3)

`SMTP_PORT=465` → implicit SSL via `smtplib.SMTP_SSL`. Any other port →
plain connect + `STARTTLS`. No separate `STARTTLS` flag exists; the port
selects the transport. Covers Gmail (587 with app password) and legacy
SSL hosts (465).

### Twilio sandbox onboarding

For WhatsApp specifically, recipients must first message the Twilio
sandbox phrase ("join <words>") before they can receive sandbox
messages. Production accounts skip this step.

## Templating

Subject and body run through `str.format`. The full variable set:

| Variable                | Source                                                          |
|-------------------------|-----------------------------------------------------------------|
| `{workspace}`           | Workspace directory name                                        |
| `{job}`                 | Job name                                                        |
| `{trigger}`             | `JobRunORM.trigger` (or `"manual"` if no row)                   |
| `{run_id}`              | Run id (synthesized as `local-<hex>` for non-trigger CLI runs)  |
| `{status}`              | **Message task's local view** — see below                       |
| `{started_at}`          | ISO 8601 with local-tz offset                                   |
| `{finished_at}`         | ISO 8601 with local-tz offset                                   |
| `{duration_seconds}`    | Float seconds between started/finished                          |
| `{failed_tasks}`        | Comma-separated names of sibling tasks that failed              |
| `{succeeded_tasks}`     | Comma-separated names of sibling tasks that succeeded           |
| `{skipped_tasks}`       | Comma-separated names of sibling tasks that were skipped        |
| `{failed_tasks_summary}`| Per-failed-task block: name + rc + last 5 lines of output       |

Excludes the message task itself from `task_results`.

### `{status}` ≠ `JobRunORM.status` (B-F2)

`{status}` is "failed" if any *non-skipped* sibling task in this run
ended `failed`, else "success". This is the **message task's local
view** at dispatch time — it does **not** mirror the engine's leaf-only
finalization for `JobRunORM.status`.

Worked example: in a 2-task DAG `landing → notify_on_failure`, when
`landing` fails and `notify_on_failure` (the message leaf) succeeds, the
template renders `{status}: failed` (because landing failed). The
engine then finalizes the run as `success` (because the only leaf
succeeded). This is intentional: the *alert* says "the pipeline
failed", the *run record* says "the alerting did its job".

To force the run failed, place an `error`-type task downstream of the
message — see `docs/subsystems/engine.md` for the leaf-only rule and
`forces_run_failure`.

### Templates are trusted (B-F5)

`str.format` walks attributes (`{run_id.__class__}` etc.). Treat
templates as trusted workspace-author input. Do **not** load templates
from untrusted sources.

## Multi-channel: strict-all (D15)

When `channels:` lists more than one, deliveries fan out in parallel.
If **any** channel raises, the task fails (rc=1) — even if the others
succeeded. Aggregated channel errors are written to the task's
`ExecucaoORM.output`.

## Interaction with leaf-only outcome (D10)

A leaf message task that **fails on a channel error** counts as a
failed leaf and flips the run to `failed`. A non-leaf message task that
fails does **not** flip the run by itself — but the leaf-only rule
still applies to anything downstream of it.

## See also

- `docs/superpowers/specs/2026-05-27-trigger-catchup-alerting-design.md` — Spec B (D7, D12–D17)
- `docs/subsystems/engine.md` — Spec A engine rules consumed here
