# Dedicated task control

These components add reconnectable control to an existing Linux training
executor. They are an integration layer, not a standalone training service;
the regular Starport and Starship installers do not deploy a compute executor.

```text
Starport controller ── authenticated SSH ── detached task supervisor
                                           ├─ existing runner
                                           ├─ task resource lease
                                           └─ logs and result bundle
```

## Integration

On the compute server, place `task_control.py` and `stream_store.py` together
in a versioned subdirectory of the existing runtime. The controller currently
expects `runtime/durable-v2/task_control.py`. Its parent runtime must provide:

- `resources.py`: the registered task and lease roots, task-ID validation,
  serialized reservation access, and verified resource release.
- `runner.py`: the existing task launcher, newline-delimited control events,
  heartbeat/cancel input, and its task cleanup protocol.

The supervisor consumes `running`, `log`, `diagnostic`, `artifact_bundle`, and
`finished` events from that runner. Task state and supervision files stay outside
the workload's mounts. The runner retains responsibility for the sandbox,
resource limits, read-only dependencies and restricted network egress.

On the control host, place `durable_backend.py` and `stream_store.py` on the
receiver's Python import path. The receiver adapter supplies its SSH/SFTP
transport, runtime and task-root configuration, archive validation, resource
release, active-worker registry, and a serialized `state(task, **updates)` hook.
Use `execute()` for new tasks and `recover_jobs()` during receiver startup.
`attached_ids()` identifies tasks with a fresh, verified control connection.

Use `isolation_toolkit.admission_recovery` to mark a receiver-owned startup
pause and lift it only after all unsettled jobs are accounted for. A subsequent
manual pause invalidates the old recovery marker. Advertise support only after
both sides are installed and validated. Switch receivers while idle so legacy
workers retain their original lifecycle.

## Runtime behavior

- Each task ID starts at most once. Repeated `start` calls attach to its existing
  supervisor; an uncertain previously issued execution is never relaunched.
- Loss of the control connection leaves the remote runner active. Reconnection
  resumes log cursors and clears the previous connection warning.
- Logs keep a 16 MiB recent buffer and up to 256 MiB of compressed segments.
  Missing retained history is reported as a gap.
- Results remain on the server, resume from partial downloads, and are checked
  by size and SHA-256 before publication.
- Confirmed remote cleanup releases resources even if the controller is offline.
  Cancellation and task limits remain effective.

This runtime uses Linux process, locking and isolation facilities. It does not
change Windows/macOS console installation, standard-queue jobs or multi-server
training. A server reboot still requires application-level checkpoint recovery.

## Validation

From the repository root on Linux:

```sh
python3 -m unittest discover -s tests -p test_durable_control.py -v
python3 -m unittest discover -s tests -p test_admission_recovery.py -v
```

The fixtures cover reconnect/idempotence, explicit cancellation, resource
release, result resumption, log replication and admission ownership without
contacting or stopping production jobs.
