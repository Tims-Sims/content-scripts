# Google Drive to Asana

This tool creates one Asana task for every file in a Google Drive folder.
Nested folders are scanned automatically. Each task uses the Drive file's exact
name and includes its Drive link and folder path in the task notes.

The tool only reads Google Drive file metadata. It cannot download, edit, or
delete the contents of your Drive files.

## What you need before the first run

- Python 3.10 or newer.
- A Google account with access to the Drive folder.
- An Asana account that can create tasks in the intended workspace or project.
- A Google OAuth desktop-client JSON file named `credentials.json`.
- An Asana personal access token (PAT).

> **Google terminology:** this script does not use a simple Google API key.
> Private Drive folders require user authorization, so Google provides an OAuth
> client ID and client secret in a downloaded JSON file. The instructions below
> show how to obtain it.

## 1. Get the Google Drive OAuth credentials

These steps follow Google's current
[Drive Python quickstart](https://developers.google.com/workspace/drive/api/quickstart/python)
and [Drive authorization guide](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

### Create or select a Google Cloud project

1. Sign in to the [Google Cloud Console](https://console.cloud.google.com/).
2. Use the project selector at the top of the page.
3. Select an existing project, or choose **New project** and create one named
   something recognizable, such as `Drive to Asana`.
4. Make sure that project remains selected for all the following steps.

### Enable the Google Drive API

1. Open the
   [Google Drive API page](https://console.cloud.google.com/apis/library/drive.googleapis.com).
2. Confirm that the correct Cloud project is selected.
3. Click **Enable**. If the button says **Manage**, the API is already enabled.

### Configure the Google Auth consent screen

1. In Google Cloud Console, open **Google Auth Platform > Branding**.
2. If prompted, click **Get started**.
3. Enter an app name such as `Drive to Asana` and select your support email.
4. Under **Audience**, choose the appropriate user type:

   - Choose **Internal** when the Cloud project and everyone using the script
     belong to the same Google Workspace organization.
   - Choose **External** for a personal Google account or users outside the
     project's Workspace organization. Leave the app in **Testing**, then open
     **Audience > Test users** and add every Google account that will run the
     script.

5. Enter a contact email, accept Google's user-data policy, and finish creating
   the app configuration.
6. Open **Google Auth Platform > Data Access**.
7. Click **Add or remove scopes**, find or manually add this scope, and save:

   ```text
   https://www.googleapis.com/auth/drive.metadata.readonly
   ```

This is a restricted but read-only metadata scope. It lets the script see file
names, IDs, folder relationships, and Drive links, but it does not grant access
to file contents. Google documents the scope classifications in its
[Drive scope guide](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

For an External app left in Testing, Google limits access to the listed test
users and its refresh tokens can expire after seven days. If that happens, run
the Google authorization step again. Internal Workspace apps do not have that
testing-token limitation. Public use by unrelated users requires Google's OAuth
verification process.

### Create and download the desktop OAuth client

1. Open **Google Auth Platform > Clients**.
2. Click **Create client**.
3. For **Application type**, choose **Desktop app**.
4. Give it a name such as `Drive to Asana desktop client`.
5. Click **Create**.
6. Download the client JSON file.
7. Rename the downloaded file to exactly `credentials.json`.
8. Place it beside `run_drive_to_asana.bat` in this repository:

   ```text
   content-scripts/
   ├── credentials.json
   ├── drive_to_asana.py
   └── run_drive_to_asana.bat
   ```

Do not send this file to anyone or commit it to Git. It contains the OAuth
client secret and is already excluded by this repository's `.gitignore`.

## 2. Get the Asana personal access token

Asana calls its API key a **personal access token**, or **PAT**. Asana recommends
PATs for individual scripts like this one. See Asana's official
[PAT guide](https://developers.asana.com/docs/personal-access-token).

1. Sign in to Asana.
2. Open the [Asana developer console](https://app.asana.com/0/my-apps).

   You can also reach it from Asana by selecting your profile photo, then
   **Settings > Apps > View developer console**.

3. Find **Personal access tokens** and click **Create new token**.
4. Enter a descriptive name such as `Google Drive task creator`.
5. Accept the Asana API terms if prompted, then click **Create token**.
6. Copy the token immediately. Asana displays it only once.
7. Keep it ready for the first run. The launcher will ask you to paste it into a
   hidden prompt.

Treat the PAT like a password. It has the same Asana access as the account that
created it, and tasks created through it are attributed to that user. Do not put
the PAT in this repository, a screenshot, email, or chat message.

## 3. Run the tool

On Windows, double-click **`run_drive_to_asana.bat`**.

The launcher will:

1. Create a Python virtual environment if needed.
2. Install any missing packages.
3. Open the guided setup and sync wizard.
4. Open a browser so you can authorize the Google account.
5. Ask for the Asana PAT using a hidden prompt.
6. Show the available Asana workspaces and projects.
7. Ask for the Google Drive folder link.
8. Preview the tasks and ask for confirmation before creating anything.

When entering the Asana token, the prompt intentionally shows no characters or
asterisks. Paste the token and press Enter.

On subsequent runs, the saved connections are reused. Normally, you only need
to select the Asana destination, paste the Drive folder link, and confirm the
preview.

You can launch the same wizard from PowerShell:

```powershell
.venv\Scripts\python.exe drive_to_asana.py
```

## Changing or reconnecting credentials

Replace the stored Asana PAT at any time:

```powershell
.venv\Scripts\python.exe drive_to_asana.py auth-asana
```

Reconnect Google Drive or choose a different Google account:

```powershell
.venv\Scripts\python.exe drive_to_asana.py auth-google --credentials credentials.json
```

The Asana PAT is stored in the operating system credential store—Windows
Credential Manager on Windows. Google authorization and duplicate-sync state
are stored outside the repository:

- Windows: `%LOCALAPPDATA%\drive-to-asana`
- macOS: `~/Library/Application Support/drive-to-asana`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/drive-to-asana`

## Duplicate behavior

After successfully creating a task, the tool records the Drive file ID and
Asana task ID locally. A later run to the same Asana destination skips that
Drive file, even if the file was renamed. This prevents accidental duplicate
tasks.

If the wizard says everything has already been synced, no new tasks are needed.
Advanced command-line users can bypass the check with `--allow-duplicates`.

## Troubleshooting

### Google says the app is blocked or access is denied

- For an External app in Testing, confirm that your Google account appears in
  **Google Auth Platform > Audience > Test users**.
- Confirm that the Drive API is enabled in the same project that owns the OAuth
  client.
- Confirm that `drive.metadata.readonly` is listed under **Data Access**.
- A Google Workspace administrator can block restricted OAuth scopes. If the
  settings above are correct, ask the administrator to allow the OAuth client or
  create the Cloud project inside the organization and use an Internal audience.

### Google authorization worked before but now fails

External Testing refresh tokens can expire after seven days. Run the Google
reconnection command from the previous section and authorize again.

### `credentials.json` cannot be found

Confirm that the file has that exact name and is in the same directory as
`run_drive_to_asana.bat`. Windows can hide extensions, so make sure the actual
name is not `credentials.json.json`.

### The Asana token is rejected

Create a replacement PAT in the Asana developer console, then run the Asana
replacement command above. Also confirm that an `ASANA_ACCESS_TOKEN` environment
variable is not overriding the stored token.

### A workspace or project is missing

The Asana PAT only sees workspaces and projects available to the user who
created it. Add that user to the required workspace/project or create the PAT
from the correct Asana account.

## Optional command-line usage

List Asana workspaces:

```powershell
.venv\Scripts\python.exe drive_to_asana.py workspaces
```

List projects in a workspace:

```powershell
.venv\Scripts\python.exe drive_to_asana.py projects --workspace-gid YOUR_WORKSPACE_GID
```

Preview a Drive folder without accessing Asana:

```powershell
.venv\Scripts\python.exe drive_to_asana.py preview "GOOGLE_DRIVE_FOLDER_LINK"
```

Perform a full dry run:

```powershell
.venv\Scripts\python.exe drive_to_asana.py sync "GOOGLE_DRIVE_FOLDER_LINK" `
  --workspace-gid YOUR_WORKSPACE_GID `
  --project-gid YOUR_PROJECT_GID `
  --dry-run
```

Create tasks non-interactively by removing `--dry-run`.

Useful options:

- `--top-level-only` ignores nested folders.
- `--unassigned` creates tasks without assigning them to the token owner.
- `--assignee USER_GID` assigns tasks to another Asana user.
- `--allow-duplicates` bypasses the local duplicate check.

## Manual Python installation

The Windows launcher performs this setup automatically. For manual setup or
other operating systems:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -v
```
