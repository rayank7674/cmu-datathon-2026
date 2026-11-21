# CMU Datathon 2026 Databricks backup

GitHub Actions reads the Databricks project recursively and mirrors eligible exports under `databricks/`. Databricks is the source of truth. Teammates continue editing there. Only the backup repository receives commits.

The exporter uses GET requests to the Workspace List and Export APIs. It writes Python, SQL, Scala and R notebooks as source, allowlisted UTF-8 workspace files, and in-folder AI/BI dashboard drafts as `.lvdash.json`. Matching objects keep their relative directory structure. Facility Field Explorer is included if it appears as a readable dashboard in the configured project tree. Empty folders are absent from Git.

The job stages a full snapshot, verifies the inventory again, then mirrors additions, changes, and deletions. It commits only when the staged Git content changes. Access, export, file-size, and suspected-secret failures stop the job before any commit. A failed push retains the previous remote snapshot and a later run starts with a fresh checkout.

The exporter skips datasets, credential-like paths, hidden items, binaries and unlisted file types. It removes outputs and attachments from Jupyter workspace files and checks eligible exports for common credential formats. This check is heuristic. Keep secrets and private records out of notebook source, dashboard queries and other files included in the mirror. Git retains old versions after source deletions.

Enter a valid Databricks credential in the repository Actions secret named `DATABRICKS_TOKEN`. Use a replacement for any token previously shared in chat. Keep its Databricks permissions limited to the reads needed by this backup. GitHub uses the job-scoped `GITHUB_TOKEN` to push to this repository.

The workflow supports manual dispatch on the default branch. To activate scheduled jobs, set repository Actions variable `DATABRICKS_SYNC_ENABLED` to `true` once a manual run is verified. The schedule is every five minutes at minute 2, 7, 12, and so on UTC, subject to GitHub queue delays. GitHub-hosted runs on private repositories use metered Actions minutes. Consecutive five-minute runs can consume about 8,640 billable minutes over 30 days even if each run takes less than one minute.
