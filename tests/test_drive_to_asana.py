import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drive_to_asana import (
    AsanaClient,
    CliError,
    DRIVE_FOLDER_MIME_TYPE,
    DriveClient,
    DriveFile,
    SyncState,
    _confirm,
    _select_item,
    _secure_write,
    build_parser,
    extract_drive_folder_id,
    sync_files,
    task_notes,
)


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeFilesResource:
    def __init__(self, root, children):
        self.root = root
        self.children = children

    def get(self, **_kwargs):
        return FakeRequest(self.root)

    def list(self, **kwargs):
        folder_id = kwargs["q"].split("'", 2)[1]
        return FakeRequest({"files": self.children.get(folder_id, [])})


class FakeDriveService:
    def __init__(self, root, children):
        self.resource = FakeFilesResource(root, children)

    def files(self):
        return self.resource


class FakeAsana:
    def __init__(self):
        self.calls = []
        self.attachments = []

    def create_task(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls)
        return {
            "gid": f"task-{index}",
            "permalink_url": f"https://app.asana.com/task/{index}",
        }

    def attach_external_url(self, **kwargs):
        self.attachments.append(kwargs)
        return {"gid": f"attachment-{len(self.attachments)}", "name": kwargs["name"]}


class StaleTaskAsana(FakeAsana):
    def __init__(self, stale_task_gid):
        super().__init__()
        self.stale_task_gid = stale_task_gid

    def attach_external_url(self, **kwargs):
        if kwargs["task_gid"] == self.stale_task_gid:
            raise CliError(
                f"Asana API error 400: parent: Unknown object: {self.stale_task_gid}"
            )
        return super().attach_external_url(**kwargs)


class FakeResponse:
    def __init__(self, status_code, payload, *, headers=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class FolderIdTests(unittest.TestCase):
    def test_extracts_common_folder_links(self):
        gid = "1AbCdEfGhIjKlMnOp"
        links = [
            f"https://drive.google.com/drive/folders/{gid}",
            f"https://drive.google.com/drive/u/0/folders/{gid}?usp=sharing",
            f"https://drive.google.com/open?id={gid}",
            gid,
        ]
        for link in links:
            with self.subTest(link=link):
                self.assertEqual(extract_drive_folder_id(link), gid)

    def test_rejects_non_drive_host(self):
        with self.assertRaises(CliError):
            extract_drive_folder_id("https://example.com/folders/1AbCdEfGhIjKlMnOp")


class WizardInputTests(unittest.TestCase):
    def test_no_command_is_valid_for_guided_mode(self):
        args = build_parser().parse_args([])
        self.assertFalse(hasattr(args, "handler"))

    @patch("builtins.input", return_value="2")
    def test_selects_numbered_item(self, _input):
        items = [
            {"gid": "workspace-one", "name": "One"},
            {"gid": "workspace-two", "name": "Two"},
        ]
        selected = _select_item(items, item_name="workspace")
        self.assertEqual(selected["gid"], "workspace-two")

    @patch("builtins.input", return_value="")
    def test_project_selection_defaults_to_my_tasks(self, _input):
        items = [{"gid": "project-one", "name": "One"}]
        self.assertIsNone(_select_item(items, item_name="project", allow_none=True))

    @patch("builtins.input", return_value="")
    def test_confirmation_defaults_to_yes(self, _input):
        self.assertTrue(_confirm("Continue?"))


class DriveTraversalTests(unittest.TestCase):
    def setUp(self):
        root = {"id": "root-folder", "name": "Root", "mimeType": DRIVE_FOLDER_MIME_TYPE}
        children = {
            "root-folder": [
                {
                    "id": "nested-folder",
                    "name": "Nested",
                    "mimeType": DRIVE_FOLDER_MIME_TYPE,
                },
                {
                    "id": "file-a-123",
                    "name": "Alpha.pdf",
                    "mimeType": "application/pdf",
                    "webViewLink": "https://a",
                },
            ],
            "nested-folder": [
                {
                    "id": "file-b-123",
                    "name": "Beta.docx",
                    "mimeType": "application/docx",
                    "webViewLink": "https://b",
                }
            ],
        }
        self.client = DriveClient(FakeDriveService(root, children))

    def test_recursively_lists_files_with_paths(self):
        files = list(self.client.iter_files("root-folder"))
        self.assertEqual([item.name for item in files], ["Alpha.pdf", "Beta.docx"])
        self.assertEqual(files[1].folder_path, "Root / Nested")

    def test_can_limit_scan_to_top_level(self):
        files = list(self.client.iter_files("root-folder", recursive=False))
        self.assertEqual([item.name for item in files], ["Alpha.pdf"])


class AsanaClientTests(unittest.TestCase):
    def test_create_task_sends_expected_fields(self):
        session = FakeSession(
            [
                FakeResponse(
                    201,
                    {
                        "data": {
                            "gid": "task-123",
                            "name": "Report.pdf",
                            "permalink_url": "https://app.asana.com/task/123",
                        }
                    },
                )
            ]
        )
        client = AsanaClient("secret-token", session=session)

        task = client.create_task(
            name="Report.pdf",
            notes="Drive link",
            workspace_gid="workspace-123",
            project_gid="project-123",
            assignee="me",
        )

        self.assertEqual(task["gid"], "task-123")
        self.assertEqual(session.headers["Authorization"], "Bearer secret-token")
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/tasks"))
        self.assertEqual(
            kwargs["json"]["data"],
            {
                "name": "Report.pdf",
                "notes": "Drive link",
                "workspace": "workspace-123",
                "projects": ["project-123"],
                "assignee": "me",
            },
        )

    def test_attaches_drive_url_as_external_attachment(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "data": {
                            "gid": "attachment-123",
                            "name": "Report.pdf",
                            "resource_subtype": "external",
                        }
                    },
                )
            ]
        )
        client = AsanaClient("secret-token", session=session)

        attachment = client.attach_external_url(
            task_gid="task-123",
            name="Report.pdf",
            url="https://drive.google.com/open?id=file-123",
        )

        self.assertEqual(attachment["gid"], "attachment-123")
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/attachments"))
        self.assertEqual(
            kwargs["files"],
            {
                "parent": (None, "task-123"),
                "resource_subtype": (None, "external"),
                "name": (None, "Report.pdf"),
                "url": (None, "https://drive.google.com/open?id=file-123"),
            },
        )

    def test_workspace_listing_follows_asana_pagination(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "data": [{"gid": "one", "name": "One"}],
                        "next_page": {"offset": "next-offset"},
                    },
                ),
                FakeResponse(
                    200,
                    {"data": [{"gid": "two", "name": "Two"}], "next_page": None},
                ),
            ]
        )

        workspaces = AsanaClient("secret-token", session=session).list_workspaces()

        self.assertEqual([item["gid"] for item in workspaces], ["one", "two"])
        self.assertEqual(session.calls[1][2]["params"]["offset"], "next-offset")


class SecureWriteTests(unittest.TestCase):
    def test_retries_a_transient_windows_file_lock(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "state.json"
            real_replace = os.replace
            calls = 0

            def locked_then_replace(source, destination):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError("locked")
                real_replace(source, destination)

            with (
                patch("drive_to_asana.os.replace", side_effect=locked_then_replace),
                patch("drive_to_asana.time.sleep") as sleep,
            ):
                _secure_write(state_path, '{"entries": {}}')

            self.assertEqual(state_path.read_text(encoding="utf-8"), '{"entries": {}}')
            self.assertEqual(calls, 2)
            sleep.assert_called_once_with(0.2)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_path = Path(self.temp.name) / "state.json"
        self.files = [
            DriveFile("file-one-123", "One.pdf", "Root", "https://drive/one"),
            DriveFile("file-two-123", "Two.pdf", "Root / Child", "https://drive/two"),
        ]

    def test_creates_tasks_and_records_state(self):
        asana = FakeAsana()
        state = SyncState(self.state_path)
        result = sync_files(
            self.files,
            asana=asana,
            state=state,
            workspace_gid="workspace-123",
            project_gid="project-123",
            report=lambda _message: None,
        )

        self.assertEqual(result.created, 2)
        self.assertEqual([call["name"] for call in asana.calls], ["One.pdf", "Two.pdf"])
        self.assertIn("Drive link: https://drive/one", asana.calls[0]["notes"])
        self.assertEqual(
            asana.attachments,
            [
                {
                    "task_gid": "task-1",
                    "name": "One.pdf",
                    "url": "https://drive/one",
                },
                {
                    "task_gid": "task-2",
                    "name": "Two.pdf",
                    "url": "https://drive/two",
                },
            ],
        )
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["entries"]), 2)
        self.assertTrue(
            all(entry["attachment_added"] for entry in payload["entries"].values())
        )

    def test_second_sync_skips_previously_created_tasks(self):
        asana = FakeAsana()
        first_state = SyncState(self.state_path)
        sync_files(
            self.files,
            asana=asana,
            state=first_state,
            workspace_gid="workspace-123",
            project_gid=None,
            report=lambda _message: None,
        )
        second_result = sync_files(
            self.files,
            asana=asana,
            state=SyncState(self.state_path),
            workspace_gid="workspace-123",
            project_gid=None,
            report=lambda _message: None,
        )

        self.assertEqual(second_result.skipped, 2)
        self.assertEqual(len(asana.calls), 2)
        self.assertEqual(len(asana.attachments), 2)

    def test_attaches_missing_url_to_a_task_created_by_an_earlier_version(self):
        asana = FakeAsana()
        state = SyncState(self.state_path)
        state.record(
            file=self.files[0],
            destination="workspace:workspace-123",
            task_gid="existing-task-123",
            task_url="https://app.asana.com/task/existing-task-123",
        )

        result = sync_files(
            [self.files[0]],
            asana=asana,
            state=state,
            workspace_gid="workspace-123",
            project_gid=None,
            report=lambda _message: None,
        )

        self.assertEqual(result.created, 0)
        self.assertEqual(result.attachments_added, 1)
        self.assertEqual(asana.calls, [])
        self.assertEqual(asana.attachments[0]["task_gid"], "existing-task-123")
        self.assertTrue(
            state.attachment_added(self.files[0].gid, "workspace:workspace-123")
        )

    def test_replaces_a_stale_task_when_the_current_token_cannot_access_it(self):
        stale_task_gid = "stale-task-123"
        asana = StaleTaskAsana(stale_task_gid)
        state = SyncState(self.state_path)
        state.record(
            file=self.files[0],
            destination="workspace:workspace-123",
            task_gid=stale_task_gid,
            task_url="https://app.asana.com/task/stale-task-123",
        )

        result = sync_files(
            [self.files[0]],
            asana=asana,
            state=state,
            workspace_gid="workspace-123",
            project_gid=None,
            report=lambda _message: None,
        )

        self.assertEqual(result.created, 1)
        self.assertEqual(result.attachments_added, 1)
        self.assertEqual(len(asana.calls), 1)
        self.assertEqual(asana.attachments[0]["task_gid"], "task-1")
        self.assertEqual(
            state.entry(self.files[0].gid, "workspace:workspace-123")["asana_task_gid"],
            "task-1",
        )

    def test_dry_run_does_not_call_asana_or_write_state(self):
        asana = FakeAsana()
        result = sync_files(
            self.files,
            asana=asana,
            state=SyncState(self.state_path),
            workspace_gid="workspace-123",
            project_gid=None,
            dry_run=True,
            report=lambda _message: None,
        )

        self.assertEqual(result.planned, 2)
        self.assertEqual(asana.calls, [])
        self.assertFalse(self.state_path.exists())

    def test_notes_contain_source_context(self):
        notes = task_notes(self.files[1])
        self.assertIn("Folder path: Root / Child", notes)
        self.assertIn("Drive file ID: file-two-123", notes)


if __name__ == "__main__":
    unittest.main()
