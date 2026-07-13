"""Create Asana tasks from the files contained in a Google Drive folder."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol
from urllib.parse import parse_qs, urlparse


APP_NAME = "drive-to-asana"
ASANA_API_BASE = "https://app.asana.com/api/1.0"
ASANA_KEYRING_SERVICE = APP_NAME
ASANA_KEYRING_USERNAME = "asana-personal-access-token"
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
DRIVE_METADATA_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"


class CliError(RuntimeError):
    """An expected error that should be shown without a traceback."""


@dataclass(frozen=True)
class DriveFile:
    """The Drive metadata needed to create one Asana task."""

    gid: str
    name: str
    folder_path: str
    web_view_link: str


@dataclass(frozen=True)
class SyncResult:
    created: int = 0
    skipped: int = 0
    planned: int = 0
    attachments_added: int = 0


class AsanaTaskCreator(Protocol):
    def create_task(
        self,
        *,
        name: str,
        notes: str,
        workspace_gid: str | None,
        project_gid: str | None,
        assignee: str | None,
    ) -> Mapping[str, Any]: ...

    def attach_external_url(
        self, *, task_gid: str, name: str, url: str
    ) -> Mapping[str, Any]: ...


def config_dir() -> Path:
    """Return a per-user directory that does not live in the repository."""

    override = os.environ.get("DRIVE_TO_ASANA_CONFIG_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / APP_NAME if base else Path.home() / ".config" / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def google_token_path() -> Path:
    return config_dir() / "google_token.json"


def sync_state_path() -> Path:
    return config_dir() / "sync_state.json"


def _secure_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(contents, encoding="utf-8")
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError as exc:
                if attempt == 4:
                    raise CliError(
                        "Windows could not update the local sync state because the file is "
                        f"locked: {path}. Close any other Drive to Asana windows and try again."
                    ) from exc
                time.sleep(0.2 * (attempt + 1))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def extract_drive_folder_id(value: str) -> str:
    """Extract a Drive folder ID from a folder URL, or accept a raw ID."""

    candidate = value.strip()
    if not candidate:
        raise CliError("The Google Drive folder link is empty.")

    if "://" not in candidate:
        if _looks_like_drive_id(candidate):
            return candidate
        raise CliError(
            "That value is not a valid Google Drive folder link or folder ID."
        )

    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if host != "drive.google.com" and not host.endswith(".drive.google.com"):
        raise CliError("The folder link must use drive.google.com.")

    segments = [segment for segment in parsed.path.split("/") if segment]
    for index, segment in enumerate(segments[:-1]):
        if segment == "folders" and _looks_like_drive_id(segments[index + 1]):
            return segments[index + 1]

    query_id = parse_qs(parsed.query).get("id", [None])[0]
    if query_id and _looks_like_drive_id(query_id):
        return query_id

    raise CliError("Could not find a folder ID in the Google Drive link.")


def _looks_like_drive_id(value: str) -> bool:
    return len(value) >= 10 and all(char.isalnum() or char in "_-" for char in value)


class DriveClient:
    """Read folder metadata recursively through a Drive API service."""

    def __init__(self, service: Any):
        self.service = service

    def iter_files(
        self, folder_id: str, *, recursive: bool = True
    ) -> Iterator[DriveFile]:
        root = self._get_file(folder_id)
        if root.get("mimeType") != DRIVE_FOLDER_MIME_TYPE:
            raise CliError(
                f"Google Drive item '{root.get('name', folder_id)}' is not a folder."
            )

        root_name = root.get("name") or "Drive folder"
        folders: deque[tuple[str, str]] = deque([(folder_id, root_name)])
        seen_folders: set[str] = set()
        seen_files: set[str] = set()

        while folders:
            current_id, current_path = folders.popleft()
            if current_id in seen_folders:
                continue
            seen_folders.add(current_id)

            children = sorted(
                self._list_children(current_id),
                key=lambda item: (
                    item.get("mimeType") != DRIVE_FOLDER_MIME_TYPE,
                    item.get("name", "").casefold(),
                ),
            )
            for child in children:
                child_id = child.get("id")
                if not child_id:
                    continue
                if child.get("mimeType") == DRIVE_FOLDER_MIME_TYPE:
                    if recursive:
                        child_path = f"{current_path} / {child.get('name') or child_id}"
                        folders.append((child_id, child_path))
                    continue
                if child_id in seen_files:
                    continue
                seen_files.add(child_id)
                yield DriveFile(
                    gid=child_id,
                    name=child.get("name") or child_id,
                    folder_path=current_path,
                    web_view_link=child.get("webViewLink")
                    or f"https://drive.google.com/open?id={child_id}",
                )

    def _get_file(self, file_id: str) -> Mapping[str, Any]:
        try:
            return (
                self.service.files()
                .get(
                    fileId=file_id,
                    fields="id,name,mimeType,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as exc:
            raise CliError(
                f"Google Drive could not open folder {file_id}: {_api_error_message(exc)}"
            ) from exc

    def _list_children(self, folder_id: str) -> list[Mapping[str, Any]]:
        children: list[Mapping[str, Any]] = []
        page_token: str | None = None
        while True:
            try:
                response = (
                    self.service.files()
                    .list(
                        q=f"'{folder_id}' in parents and trashed = false",
                        spaces="drive",
                        fields="nextPageToken,files(id,name,mimeType,webViewLink)",
                        orderBy="folder,name_natural",
                        pageSize=100,
                        pageToken=page_token,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute()
                )
            except Exception as exc:
                raise CliError(
                    f"Google Drive could not list the contents of folder {folder_id}: "
                    f"{_api_error_message(exc)}"
                ) from exc
            children.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return children


def _api_error_message(exc: Exception) -> str:
    content = getattr(exc, "content", None)
    if isinstance(content, bytes):
        try:
            payload = json.loads(content.decode("utf-8"))
            return payload.get("error", {}).get("message") or str(exc)
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
    return str(exc)


class AsanaClient:
    """Small Asana REST client with pagination and retry handling."""

    def __init__(self, token: str, *, session: Any | None = None):
        if not token.strip():
            raise CliError("The Asana personal access token is empty.")
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise CliError(
                    "Missing dependency 'requests'. Run: python -m pip install -r requirements.txt"
                ) from exc
            session = requests.Session()
        self.session = session
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token.strip()}",
                "Accept": "application/json",
                "User-Agent": f"{APP_NAME}/1.0",
            }
        )

    def current_user(self) -> Mapping[str, Any]:
        return self._request("GET", "/users/me")["data"]

    def list_workspaces(self) -> list[Mapping[str, Any]]:
        return list(
            self._paginated("/workspaces", params={"limit": 100, "opt_fields": "name"})
        )

    def list_projects(self, workspace_gid: str) -> list[Mapping[str, Any]]:
        return list(
            self._paginated(
                f"/workspaces/{workspace_gid}/projects",
                params={"limit": 100, "archived": "false", "opt_fields": "name"},
            )
        )

    def create_task(
        self,
        *,
        name: str,
        notes: str,
        workspace_gid: str | None,
        project_gid: str | None,
        assignee: str | None,
    ) -> Mapping[str, Any]:
        data: dict[str, Any] = {"name": name, "notes": notes}
        if workspace_gid:
            data["workspace"] = workspace_gid
        if project_gid:
            data["projects"] = [project_gid]
        if assignee:
            data["assignee"] = assignee
        return self._request(
            "POST",
            "/tasks",
            params={"opt_fields": "gid,name,permalink_url"},
            json={"data": data},
        )["data"]

    def attach_external_url(
        self, *, task_gid: str, name: str, url: str
    ) -> Mapping[str, Any]:
        """Add a Drive URL to a task as an Asana external attachment."""

        for attempt in range(3):
            try:
                return self._request(
                    "POST",
                    "/attachments",
                    params={"opt_fields": "gid,name,resource_subtype,permanent_url"},
                    files={
                        "parent": (None, task_gid),
                        "resource_subtype": (None, "external"),
                        "name": (None, name),
                        "url": (None, url),
                    },
                )["data"]
            except CliError as exc:
                if attempt == 2 or not _is_unknown_attachment_parent_error(exc):
                    raise
                time.sleep(attempt + 1)

        raise CliError("Asana attachment creation failed after several attempts.")

    def _paginated(
        self, path: str, *, params: Mapping[str, Any]
    ) -> Iterator[Mapping[str, Any]]:
        request_params = dict(params)
        while True:
            payload = self._request("GET", path, params=request_params)
            yield from payload.get("data", [])
            next_page = payload.get("next_page")
            if not next_page or not next_page.get("offset"):
                return
            request_params["offset"] = next_page["offset"]

    def _request(self, method: str, path: str, **kwargs: Any) -> Mapping[str, Any]:
        url = f"{ASANA_API_BASE}{path}"
        for attempt in range(4):
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)
            except Exception as exc:
                if attempt == 3:
                    raise CliError(f"Could not connect to Asana: {exc}") from exc
                time.sleep(2**attempt)
                continue

            if response.status_code < 400:
                try:
                    return response.json()
                except (ValueError, AttributeError) as exc:
                    raise CliError("Asana returned an invalid response.") from exc

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = (
                            min(float(retry_after), 30) if retry_after else 2**attempt
                        )
                    except ValueError:
                        delay = 2**attempt
                    time.sleep(delay)
                    continue

            raise CliError(_asana_error_message(response))

        raise CliError("Asana request failed after several attempts.")


def _asana_error_message(response: Any) -> str:
    try:
        payload = response.json()
        messages = [
            item.get("message", "")
            for item in payload.get("errors", [])
            if item.get("message")
        ]
    except (ValueError, AttributeError):
        messages = []
    detail = "; ".join(messages) or getattr(response, "text", "") or "Unknown error"
    if response.status_code == 401:
        detail = f"Authentication failed. Replace the Asana token with 'auth-asana'. {detail}"
    return f"Asana API error {response.status_code}: {detail}"


def _is_unknown_attachment_parent_error(error: CliError) -> bool:
    return "parent: unknown object" in str(error).lower()


class SyncState:
    """Local idempotency state keyed by Drive file and Asana destination."""

    def __init__(self, path: Path | None = None):
        self.path = path or sync_state_path()
        self.entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if payload.get("version") == 1 and isinstance(
                    payload.get("entries"), dict
                ):
                    self.entries = payload["entries"]
            except (OSError, json.JSONDecodeError, AttributeError):
                raise CliError(
                    f"The sync state file is invalid: {self.path}. Move or delete it, then try again."
                )

    @staticmethod
    def key(file_gid: str, destination: str) -> str:
        return f"{destination}|{file_gid}"

    def contains(self, file_gid: str, destination: str) -> bool:
        return self.key(file_gid, destination) in self.entries

    def entry(self, file_gid: str, destination: str) -> Mapping[str, Any] | None:
        return self.entries.get(self.key(file_gid, destination))

    def attachment_added(self, file_gid: str, destination: str) -> bool:
        entry = self.entry(file_gid, destination)
        return bool(entry and entry.get("attachment_added"))

    def record(
        self,
        *,
        file: DriveFile,
        destination: str,
        task_gid: str,
        task_url: str | None,
    ) -> None:
        self.entries[self.key(file.gid, destination)] = {
            "drive_file_id": file.gid,
            "drive_file_name": file.name,
            "destination": destination,
            "asana_task_gid": task_gid,
            "asana_task_url": task_url,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "attachment_added": False,
        }
        self.save()

    def mark_attachment_added(self, file_gid: str, destination: str) -> None:
        entry = self.entry(file_gid, destination)
        if not entry:
            raise CliError(
                "Cannot record an attachment for a task that is not in sync state."
            )
        entry["attachment_added"] = True
        entry["attachment_added_at"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def remove(self, file_gid: str, destination: str) -> None:
        self.entries.pop(self.key(file_gid, destination), None)
        self.save()

    def save(self) -> None:
        _secure_write(
            self.path,
            json.dumps(
                {"version": 1, "entries": self.entries}, indent=2, sort_keys=True
            )
            + "\n",
        )


def task_notes(file: DriveFile) -> str:
    return (
        "Created automatically from Google Drive.\n\n"
        f"Drive file: {file.name}\n"
        f"Folder path: {file.folder_path}\n"
        f"Drive link: {file.web_view_link}\n"
        f"Drive file ID: {file.gid}"
    )


def sync_files(
    files: Iterable[DriveFile],
    *,
    asana: AsanaTaskCreator,
    state: SyncState,
    workspace_gid: str | None,
    project_gid: str | None,
    assignee: str | None = "me",
    allow_duplicates: bool = False,
    dry_run: bool = False,
    report: Callable[[str], None] = print,
) -> SyncResult:
    if not workspace_gid and not project_gid:
        raise CliError(
            "Provide --workspace-gid or --project-gid as the Asana destination."
        )

    destination = (
        f"project:{project_gid}" if project_gid else f"workspace:{workspace_gid}"
    )
    created = skipped = planned = attachments_added = 0
    for file in files:
        existing_entry = state.entry(file.gid, destination)
        if not allow_duplicates and existing_entry:
            if state.attachment_added(file.gid, destination):
                report(f"SKIP    {file.name} (already synced)")
                skipped += 1
                continue

            task_gid = str(existing_entry.get("asana_task_gid") or "")
            if not task_gid:
                raise CliError(
                    f"The sync state for '{file.name}' has no Asana task ID. "
                    "Remove that entry from the sync state file and run again."
                )
            if dry_run:
                report(f"ATTACH  {file.name}  [{file.folder_path}]")
                planned += 1
                continue
            try:
                asana.attach_external_url(
                    task_gid=task_gid,
                    name=file.name,
                    url=file.web_view_link,
                )
            except CliError as exc:
                if not _is_unknown_attachment_parent_error(exc):
                    raise
                report(
                    f"RECREATE {file.name} (the previously synced Asana task is unavailable)"
                )
                state.remove(file.gid, destination)
            else:
                state.mark_attachment_added(file.gid, destination)
                report(f"ATTACHED {file.name} (existing task)")
                attachments_added += 1
                continue

        if dry_run:
            report(f"CREATE  {file.name}  [{file.folder_path}]")
            planned += 1
            continue

        task = asana.create_task(
            name=file.name,
            notes=task_notes(file),
            workspace_gid=workspace_gid,
            project_gid=project_gid,
            assignee=assignee,
        )
        task_gid = str(task.get("gid") or "")
        if not task_gid:
            raise CliError(f"Asana created '{file.name}' but did not return a task ID.")
        state.record(
            file=file,
            destination=destination,
            task_gid=task_gid,
            task_url=task.get("permalink_url"),
        )
        asana.attach_external_url(
            task_gid=task_gid,
            name=file.name,
            url=file.web_view_link,
        )
        state.mark_attachment_added(file.gid, destination)
        report(
            f"CREATED {file.name}"
            + (f"  {task['permalink_url']}" if task.get("permalink_url") else "")
        )
        created += 1
        attachments_added += 1

    return SyncResult(
        created=created,
        skipped=skipped,
        planned=planned,
        attachments_added=attachments_added,
    )


def load_google_credentials() -> Any:
    token_path = google_token_path()
    if not token_path.exists():
        raise CliError(
            "Google Drive is not connected. Run: python drive_to_asana.py auth-google --credentials credentials.json"
        )
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise CliError(
            "Missing Google dependencies. Run: python -m pip install -r requirements.txt"
        ) from exc

    try:
        credentials = Credentials.from_authorized_user_file(
            str(token_path), [DRIVE_METADATA_SCOPE]
        )
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _secure_write(token_path, credentials.to_json())
    except Exception as exc:
        raise CliError(
            f"Could not load Google authorization: {exc}. Run 'auth-google' again."
        ) from exc
    if not credentials.valid:
        raise CliError(
            "Google authorization is no longer valid. Run 'auth-google' again."
        )
    return credentials


def authorize_google(credentials_path: Path) -> Mapping[str, Any]:
    if not credentials_path.is_file():
        raise CliError(f"Google OAuth credentials file not found: {credentials_path}")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise CliError(
            "Missing Google dependencies. Run: python -m pip install -r requirements.txt"
        ) from exc
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(credentials_path), [DRIVE_METADATA_SCOPE]
        )
        credentials = flow.run_local_server(port=0)
    except Exception as exc:
        raise CliError(f"Google authorization failed: {exc}") from exc
    _secure_write(google_token_path(), credentials.to_json())
    return {"token_path": str(google_token_path())}


def build_drive_client() -> DriveClient:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise CliError(
            "Missing Google dependencies. Run: python -m pip install -r requirements.txt"
        ) from exc
    credentials = load_google_credentials()
    return DriveClient(
        build("drive", "v3", credentials=credentials, cache_discovery=False)
    )


def get_asana_token() -> str:
    environment_token = os.environ.get("ASANA_ACCESS_TOKEN", "").strip()
    if environment_token:
        return environment_token
    try:
        import keyring
    except ImportError as exc:
        raise CliError(
            "Missing dependency 'keyring'. Run: python -m pip install -r requirements.txt"
        ) from exc
    try:
        token = keyring.get_password(ASANA_KEYRING_SERVICE, ASANA_KEYRING_USERNAME)
    except Exception as exc:
        raise CliError(
            "Could not read the operating system credential store. "
            "Set ASANA_ACCESS_TOKEN or fix the keyring backend."
        ) from exc
    if not token:
        raise CliError(
            "Asana is not connected. Run: python drive_to_asana.py auth-asana"
        )
    return token


def save_asana_token(token: str) -> None:
    try:
        import keyring
    except ImportError as exc:
        raise CliError(
            "Missing dependency 'keyring'. Run: python -m pip install -r requirements.txt"
        ) from exc
    try:
        keyring.set_password(ASANA_KEYRING_SERVICE, ASANA_KEYRING_USERNAME, token)
    except Exception as exc:
        raise CliError(
            "Could not write to the operating system credential store. "
            "You can set ASANA_ACCESS_TOKEN instead."
        ) from exc


def _authorize_asana_interactively() -> tuple[AsanaClient, Mapping[str, Any]]:
    token = getpass.getpass("Asana personal access token (input is hidden): ").strip()
    if not token:
        raise CliError("No Asana token was entered.")
    client = AsanaClient(token)
    user = client.current_user()
    save_asana_token(token)
    return client, user


def _command_auth_asana(_args: argparse.Namespace) -> int:
    _client, user = _authorize_asana_interactively()
    print(f"Asana connected as {user.get('name', user.get('gid', 'current user'))}.")
    print("Re-run auth-asana at any time to replace this token.")
    return 0


def _command_auth_google(args: argparse.Namespace) -> int:
    result = authorize_google(args.credentials.expanduser().resolve())
    print(f"Google Drive connected. Authorization saved to {result['token_path']}")
    return 0


def _command_workspaces(_args: argparse.Namespace) -> int:
    workspaces = AsanaClient(get_asana_token()).list_workspaces()
    if not workspaces:
        print("No accessible Asana workspaces found.")
    for workspace in workspaces:
        print(f"{workspace.get('gid')}\t{workspace.get('name')}")
    return 0


def _command_projects(args: argparse.Namespace) -> int:
    projects = AsanaClient(get_asana_token()).list_projects(args.workspace_gid)
    if not projects:
        print("No accessible, active Asana projects found in that workspace.")
    for project in projects:
        print(f"{project.get('gid')}\t{project.get('name')}")
    return 0


def _command_preview(args: argparse.Namespace) -> int:
    folder_id = extract_drive_folder_id(args.folder)
    files = list(
        build_drive_client().iter_files(folder_id, recursive=not args.top_level_only)
    )
    if not files:
        print("No files found in the selected Drive folder.")
        return 0
    for file in files:
        print(f"{file.name}\t{file.folder_path}\t{file.web_view_link}")
    print(f"\n{len(files)} task(s) would be created.")
    return 0


def _command_sync(args: argparse.Namespace) -> int:
    if not args.workspace_gid and not args.project_gid:
        raise CliError(
            "Provide --workspace-gid or --project-gid as the Asana destination."
        )
    folder_id = extract_drive_folder_id(args.folder)
    files = list(
        build_drive_client().iter_files(folder_id, recursive=not args.top_level_only)
    )
    if not files:
        print("No files found in the selected Drive folder.")
        return 0

    assignee = None if args.unassigned else args.assignee
    result = sync_files(
        files,
        asana=AsanaClient(get_asana_token()),
        state=SyncState(),
        workspace_gid=args.workspace_gid,
        project_gid=args.project_gid,
        assignee=assignee,
        allow_duplicates=args.allow_duplicates,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(
            f"\nDry run: {result.planned} change(s) planned, {result.skipped} already synced."
        )
    else:
        print(
            f"\nDone: {result.created} created, {result.attachments_added} Drive links "
            f"attached, {result.skipped} already synced."
        )
    return 0


def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError as exc:
        raise CliError("Input was closed before setup was completed.") from exc


def _select_item(
    items: list[Mapping[str, Any]],
    *,
    item_name: str,
    allow_none: bool = False,
) -> Mapping[str, Any] | None:
    if not items and allow_none:
        return None
    if not items:
        raise CliError(f"No accessible Asana {item_name}s were found.")

    print()
    print(f"Available Asana {item_name}s:")
    if allow_none:
        print(f"0. My Tasks only (do not add tasks to an Asana {item_name})")
    for index, item in enumerate(items, start=1):
        print(f"{index}. {item.get('name', 'Unnamed')}  [{item.get('gid')}]")

    default = "0" if allow_none else "1"
    while True:
        raw_choice = _input(
            f"Choose an Asana {item_name} by number [{default}]: "
        ).strip()
        choice = raw_choice or default
        if allow_none and choice == "0":
            return None
        try:
            index = int(choice)
        except ValueError:
            index = -1
        if 1 <= index <= len(items):
            return items[index - 1]
        print(f"Enter a number from {'0' if allow_none else '1'} to {len(items)}.")


def _confirm(prompt: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = _input(f"{prompt} {suffix}: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter y or n.")


def _wizard_drive_client() -> DriveClient:
    try:
        client = build_drive_client()
        print("Google Drive connection: ready")
        return client
    except CliError as exc:
        print(f"Google Drive needs to be connected: {exc}")

    raw_path = _input(
        "Path to Google OAuth credentials JSON [credentials.json]: "
    ).strip()
    credentials_path = Path(raw_path or "credentials.json").expanduser().resolve()
    authorize_google(credentials_path)
    print("Google Drive connected successfully.")
    return build_drive_client()


def _wizard_asana_client() -> tuple[AsanaClient, Mapping[str, Any]]:
    try:
        client = AsanaClient(get_asana_token())
        user = client.current_user()
        print(f"Asana connection: {user.get('name', user.get('gid', 'ready'))}")
        return client, user
    except CliError as exc:
        print(f"Asana needs to be connected: {exc}")

    client, user = _authorize_asana_interactively()
    if os.environ.get("ASANA_ACCESS_TOKEN"):
        print(
            "Note: ASANA_ACCESS_TOKEN is set in the environment and will take "
            "precedence on later runs."
        )
    print(f"Asana connected as {user.get('name', user.get('gid', 'current user'))}.")
    return client, user


def _command_wizard() -> int:
    print("Google Drive to Asana")
    print("This wizard creates one Asana task for every file in a Drive folder.\n")

    drive = _wizard_drive_client()
    asana, _user = _wizard_asana_client()

    print("\nLoading your accessible Asana workspaces...")
    workspaces = asana.list_workspaces()
    workspace = _select_item(workspaces, item_name="workspace")
    if workspace is None:
        raise CliError("An Asana workspace is required.")
    workspace_gid = str(workspace.get("gid") or "")
    print(f"Selected workspace: {workspace.get('name')}  [{workspace_gid}]")

    print("\nLoading projects in the selected workspace...")
    projects = asana.list_projects(workspace_gid)
    project = _select_item(projects, item_name="project", allow_none=True)
    project_gid = str(project.get("gid")) if project else None
    if project:
        print(f"Selected project: {project.get('name')}  [{project_gid}]")
    else:
        print("Selected destination: My Tasks")

    print()
    folder_link = _input("Paste the Google Drive folder link: ").strip()
    folder_id = extract_drive_folder_id(folder_link)
    print("Scanning Google Drive folders...")
    files = list(drive.iter_files(folder_id, recursive=True))
    if not files:
        print("No files were found, so no Asana tasks were created.")
        return 0

    state = SyncState()
    destination = (
        f"project:{project_gid}" if project_gid else f"workspace:{workspace_gid}"
    )
    new_task_files = [
        file for file in files if not state.contains(file.gid, destination)
    ]
    attachment_only_files = [
        file
        for file in files
        if state.contains(file.gid, destination)
        and not state.attachment_added(file.gid, destination)
    ]
    pending_files = new_task_files + attachment_only_files
    skipped_count = len(files) - len(pending_files)

    destination_name = project.get("name") if project else "My Tasks"
    print(f"\nDestination: {workspace.get('name')} / {destination_name}")
    print(f"Files found: {len(files)}")
    if skipped_count:
        print(f"Already synced and skipped: {skipped_count}")
    print(f"New tasks to create: {len(new_task_files)}")
    if attachment_only_files:
        print(
            f"Existing tasks missing a Drive attachment: {len(attachment_only_files)}"
        )

    for file in pending_files[:20]:
        print(f"  - {file.name}  [{file.folder_path}]")
    if len(pending_files) > 20:
        print(f"  ...and {len(pending_files) - 20} more")

    if not pending_files:
        print("Everything in this folder has already been synced.")
        return 0
    if not _confirm("Apply these Asana changes now?"):
        print("Cancelled. No tasks were created or updated.")
        return 0

    result = sync_files(
        files,
        asana=asana,
        state=state,
        workspace_gid=workspace_gid,
        project_gid=project_gid,
        assignee="me",
    )
    print(
        f"\nDone: {result.created} created, {result.attachments_added} Drive links "
        f"attached, {result.skipped} already synced."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create one Asana task for every file in a Google Drive folder.",
        epilog="Run without a command to start the guided setup and sync wizard.",
    )
    subcommands = parser.add_subparsers(dest="command")

    auth_asana = subcommands.add_parser(
        "auth-asana", help="Store or replace the Asana personal access token."
    )
    auth_asana.set_defaults(handler=_command_auth_asana)

    auth_google = subcommands.add_parser(
        "auth-google", help="Authorize read-only Google Drive metadata access."
    )
    auth_google.add_argument(
        "--credentials",
        type=Path,
        default=Path("credentials.json"),
        help="Google OAuth desktop-app client JSON (default: credentials.json).",
    )
    auth_google.set_defaults(handler=_command_auth_google)

    workspaces = subcommands.add_parser(
        "workspaces", help="List available Asana workspace GIDs."
    )
    workspaces.set_defaults(handler=_command_workspaces)

    projects = subcommands.add_parser(
        "projects", help="List active Asana project GIDs in a workspace."
    )
    projects.add_argument("--workspace-gid", required=True)
    projects.set_defaults(handler=_command_projects)

    preview = subcommands.add_parser(
        "preview", help="Show the tasks that a Drive folder would produce."
    )
    preview.add_argument("folder", help="Google Drive folder link or folder ID.")
    preview.add_argument(
        "--top-level-only", action="store_true", help="Do not scan nested folders."
    )
    preview.set_defaults(handler=_command_preview)

    sync = subcommands.add_parser(
        "sync", help="Create Asana tasks from a Drive folder."
    )
    sync.add_argument("folder", help="Google Drive folder link or folder ID.")
    sync.add_argument(
        "--workspace-gid",
        help="Asana workspace GID; required if no project GID is supplied.",
    )
    sync.add_argument(
        "--project-gid", help="Optional Asana project GID to receive the tasks."
    )
    sync.add_argument(
        "--assignee", default="me", help="Asana user GID (default: the token owner)."
    )
    sync.add_argument(
        "--unassigned", action="store_true", help="Create tasks without an assignee."
    )
    sync.add_argument(
        "--top-level-only", action="store_true", help="Do not scan nested folders."
    )
    sync.add_argument(
        "--allow-duplicates",
        action="store_true",
        help="Create again even if this tool already synced a file.",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Show intended changes without creating tasks.",
    )
    sync.set_defaults(handler=_command_sync)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if not hasattr(args, "handler"):
            return _command_wizard()
        return args.handler(args)
    except CliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
