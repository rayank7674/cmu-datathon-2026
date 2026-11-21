"""Export the allowlisted Databricks workspace tree into an empty directory."""

import argparse
import base64
from collections import Counter
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOST = "https://dbc-b2aac744-3b23.cloud.databricks.com"
ROOT = "/Users/rayankha08@gmail.com/CMU Datathon 2026"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 14 * 1024 * 1024
SOURCE_SUFFIX = {"PYTHON": ".py", "SQL": ".sql", "SCALA": ".scala", "R": ".r"}
TEXT_SUFFIXES = {
    ".py", ".sql", ".scala", ".r", ".md", ".rst", ".ipynb",
    ".yaml", ".yml", ".toml", ".json", ".js", ".ts", ".sh",
}
EXCLUDED = {"data", "datasets", "secrets", "credentials", "exports", "__pycache__"}
SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:dapi[a-f0-9]{24,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})\b|"
    r"(?i:\b(?:password|client_secret|api_key|access_token|databricks_token)\b"
    r"""["']?\s*[:=]\s*["'][^"'{}\r\n]{8,}["'])"""
)


class SyncError(Exception):
    """An error safe to report in workflow logs."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Workspace:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect())

    def get(self, action, **params):
        request = Request(
            f"{HOST}/api/2.0/workspace/{action}?{urlencode(params)}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            method="GET",
        )
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=20) as response:
                    data = response.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise SyncError(f"Workspace {action} response exceeds the size limit")
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise SyncError(f"Unexpected Workspace {action} response")
                return payload
            except HTTPError as error:
                reason = f"HTTP {error.code}"
                retry = error.code in {408, 429, 500, 502, 503, 504}
            except (URLError, TimeoutError):
                reason = "network error or timeout"
                retry = True
            if not retry or attempt == 2:
                raise SyncError(f"Workspace {action} failed ({reason})")
            time.sleep(2 ** attempt)


def inventory(workspace):
    pending = [ROOT]
    found = {}
    while pending:
        directory = pending.pop()
        payload = workspace.get("list", path=directory)
        if payload.get("next_page_token"):
            raise SyncError("Workspace returned a paginated listing; refusing a partial mirror")
        objects = payload.get("objects", [])
        if not isinstance(objects, list):
            raise SyncError("Workspace returned an invalid object list")
        for entry in objects:
            path = entry["path"]
            if not path.startswith(directory + "/"):
                raise SyncError("Workspace returned an object outside its listed directory")
            name = path[len(directory) + 1:]
            if (not name or "/" in name or "\\" in name or name in {".", ".."}
                    or any(ord(char) < 32 for char in name)):
                raise SyncError("Workspace returned an invalid path component")
            if (name.startswith(".") or name.casefold() in EXCLUDED
                    or re.search(r"(?i)(?:^|[._ -])(?:secrets?|credentials?|tokens?)(?:[._ -]|$)", name)):
                continue
            if path in found:
                raise SyncError("Workspace listing contained a duplicate path")
            found[path] = entry
            if entry["object_type"] in {"DIRECTORY", "REPO"}:
                pending.append(path)
    return found


def export_plan(entry):
    relative = PurePosixPath(entry["path"][len(ROOT) + 1:])
    kind = entry["object_type"]
    if kind == "NOTEBOOK":
        suffix = SOURCE_SUFFIX.get(entry.get("language"))
        if suffix is None:
            return None
        if relative.suffix.lower() != suffix:
            relative = relative.with_name(relative.name + suffix)
        return relative, "SOURCE"
    if kind == "DASHBOARD":
        if not relative.name.endswith(".lvdash.json"):
            relative = relative.with_name(relative.name + ".lvdash.json")
        return relative, "AUTO"
    if kind == "FILE" and relative.suffix.lower() in TEXT_SUFFIXES:
        return relative, "AUTO"
    return None


def cleaned_content(payload, relative, token, kind):
    if not isinstance(payload.get("content"), str):
        raise SyncError("Workspace export response did not contain file contents")
    raw = base64.b64decode(payload["content"], validate=True)
    if len(raw) > MAX_FILE_BYTES:
        raise SyncError("An eligible export exceeds 2 MiB; previous backup retained")
    text = raw.decode("utf-8-sig")
    if "\x00" in text:
        raise SyncError("An eligible export is not plain text")
    inspect_text = text
    if relative.suffix.lower() in {".json", ".ipynb"}:
        document = json.loads(text)
        inspect_text = json.dumps(document, ensure_ascii=False)
        if relative.suffix.lower() == ".ipynb":
            for cell in document["cells"]:
                if cell.get("cell_type") == "code":
                    cell["outputs"] = []
                    cell["execution_count"] = None
                cell.pop("attachments", None)
                cell["metadata"] = {}
            document["metadata"] = {}
            text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            inspect_text = text
        elif kind == "DASHBOARD" or relative.name.endswith(".lvdash.json"):
            if not isinstance(document, dict):
                raise SyncError("Workspace did not return a dashboard JSON definition")
            text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if token in text or token in inspect_text or SECRET.search(text) or SECRET.search(inspect_text):
        raise SyncError("Sensitive material detected; no snapshot will be committed")
    return text


def snapshot(workspace, output):
    before = inventory(workspace)
    counts = Counter()
    names = set()
    for entry in sorted(before.values(), key=lambda item: item["path"]):
        plan = export_plan(entry)
        if plan is None:
            if entry["object_type"] not in {"DIRECTORY", "REPO"}:
                counts["skipped"] += 1
            continue
        relative, export_format = plan
        key = relative.as_posix().casefold()
        if key in names:
            raise SyncError("Multiple workspace objects map to the same output file")
        names.add(key)
        payload = workspace.get("export", path=entry["path"], format=export_format)
        kind = entry["object_type"]
        content = cleaned_content(payload, relative, workspace.token, kind)
        target = output.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        counts[kind.lower()] += 1
    if before != inventory(workspace):
        raise SyncError("Workspace changed during export; retrying on the next run")
    return counts


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Existing empty staging directory")
    args = parser.parse_args()
    token = os.environ.get("DATABRICKS_TOKEN")
    if not token:
        parser.exit(1, "DATABRICKS_TOKEN is missing.\n")
    if not args.output.is_dir() or args.output.is_symlink() or any(args.output.iterdir()):
        parser.exit(1, "The export requires a fresh, empty staging directory.\n")
    try:
        counts = snapshot(Workspace(token), args.output)
    except SyncError as error:
        parser.exit(1, f"{error}\n")
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        parser.exit(1, f"Export stopped ({type(error).__name__}); no snapshot committed.\n")
    logging.info(
        "Staged %d notebooks, %d workspace files, %d dashboards. Skipped %d unsupported items.",
        counts["notebook"], counts["file"], counts["dashboard"], counts["skipped"],
    )


if __name__ == "__main__":
    main()
