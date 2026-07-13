# Google Drive to Asana

This tool creates one Asana task for every file in a Google Drive folder.
Nested folders are scanned automatically. Each task uses the Drive file's exact
name, includes its Drive link and folder path in the task notes, and adds the
Drive URL as an Asana attachment.

The tool only reads Google Drive file metadata. It cannot download, edit, or
delete the contents of your Drive files.

## Browser app for GitHub Pages

[`index.html`](index.html) is a self-contained browser version. Upload or
publish that one file to GitHub Pages; it has no build step, server, database,
or API-key file. It lets each visitor authorize their own Google Drive and
provide their own Asana token.

The page calls the Google Drive and Asana APIs directly from the browser. It is
well suited to a personal tool or a small trusted group. It is **not** a safe
place to embed a shared Asana token, Google client secret, or any other secret:
GitHub Pages is public static hosting, so anything put in `index.html` can be
read by anyone who can load the site.

### Browser app setup

#### 1. Create a Google OAuth web client

This uses Google OAuth, not a simple Google API key. Google documents the
[JavaScript Drive quickstart](https://developers.google.com/workspace/drive/api/quickstart/js)
and explains that browser applications use a **Web application** OAuth client
without a client secret.

1. Create or choose a project in the
   [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com).
3. In **Google Auth Platform**, configure **Branding**, **Audience**, and
   **Data Access**. Add this read-only metadata scope:

   ```text
   https://www.googleapis.com/auth/drive.metadata.readonly
   ```

4. If the audience is **External** and the app is in **Testing**, add every
   person who will use the page under **Audience > Test users**.
5. Open **Google Auth Platform > Clients**, select **Create client**, and
   choose **Web application**.
6. Under **Authorized JavaScript origins**, add your GitHub Pages origin. For
   example, for either a user site or a project site hosted at
   `https://octo-user.github.io/...`, add exactly:

   ```text
   https://octo-user.github.io
   ```

   The origin is only the scheme and hostname—do not include the repository
   path. Add `http://localhost` separately if you also test the page locally.
7. Copy the OAuth **client ID**. It ends in
   `.apps.googleusercontent.com`. Do not download or use a client-secret JSON
   file for the browser page.

The user will sign in to Google in a pop-up and grant the metadata permission.
That grants access to Drive folders shared with that user as well as folders
they created, but only within their existing Google Drive permissions.

#### 2. Create an Asana personal access token

1. Open the [Asana developer console](https://app.asana.com/0/my-apps) while
   signed in.
2. Under **Personal access tokens**, choose **Create new token**.
3. Give it a descriptive name, create it, and copy it immediately.

Asana describes a PAT as a long-lived credential with the same Asana access as
the account that generated it. See its official
[authentication guide](https://developers.asana.com/docs/authentication).
Treat it like a password.

#### 3. Publish the one HTML file

1. Commit [`index.html`](index.html) at the root of the GitHub repository.
2. On GitHub, open **Settings > Pages**.
3. Choose **Deploy from a branch**, select the branch (normally `main`) and
   the `/(root)` folder, then save.
4. Open the published URL after GitHub Pages finishes deploying.

GitHub Pages serves static HTML, CSS, and JavaScript directly from a repository,
as described in [GitHub's Pages documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).

#### 4. Use the page

1. Paste the Google OAuth web client ID and your Asana PAT.
2. If this is your own trusted browser, optionally select **Remember my Asana
   token** and save the settings. Otherwise, leave it unchecked.
3. Select **Connect Google Drive**, then complete Google's consent pop-up.
4. Select **Load Asana workspaces**, then choose a workspace and optional
   project.
5. Paste a Google Drive folder link, scan the files, review the preview, and
   create the tasks.

The Google access token is kept only in the open tab and must be reauthorized
after it expires. The optional Asana PAT and duplicate-sync history are stored
only in that browser's local storage for this Pages site. Clearing browser data,
using another browser/device, or using the page under a different Pages origin
creates a new sync history and can therefore create duplicate tasks.

### Browser reliability behavior

- The scan uses Drive's shared-drive options, so it includes folders and files
  shared with the signed-in Google user when that user already has access.
- The page lists the Asana workspaces available to the current PAT; changing
  the token clears the selected destination and requires a fresh workspace
  lookup.
- It saves a task record before adding the external Drive-link attachment. If
  the attachment call fails, the next run retries that attachment instead of
  creating a second task.
- Before skipping a prior record, it checks that the corresponding Asana task
  is still accessible. A deleted task or a task unavailable to a replacement
  PAT is recreated and the browser record is updated.
- Sync state is browser local storage, not a Windows `sync_state.json` file,
  so the prior temporary-file access-denied problem cannot occur in the browser
  version. If browser storage is disabled or full, the page reports that
  explicitly instead of continuing without duplicate protection.
- Asana rate-limit (`429`) responses are retried using the service's requested
  delay. Other API failures are shown in the activity log without exposing the
  token.

Google verification and Google Workspace administrator policies are external
controls that a page cannot override. For an External OAuth app in Testing, add
each user as a test user; broader public access to this restricted Drive scope
requires Google's applicable verification process.

For a multi-user public product with durable logins, replace the PAT field with
an OAuth flow backed by a secure server. Asana's OAuth token exchange requires
a client secret and Asana explicitly says that secret must never be exposed to
the browser; a GitHub Pages-only site cannot provide that backend securely.

## Optional Python script

The original local Python launcher remains available for offline/local use.

### What you need before the first run

- Python 3.10 or newer.
- A Google account with access to the Drive folder.
- An Asana account that can create tasks in the intended workspace or project.
- A Google OAuth desktop-client JSON file named `credentials.json`.
- An Asana personal access token (PAT).

> **Google terminology:** this script does not use a simple Google API key.
> Private Drive folders require user authorization, so Google provides an OAuth
> client ID and client secret in a downloaded JSON file. The instructions below
> show how to obtain it.

### 1. Get the Google Drive OAuth credentials

These steps follow Google's current
[Drive Python quickstart](https://developers.google.com/workspace/drive/api/quickstart/python)
and [Drive authorization guide](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

#### Create or select a Google Cloud project

1. Sign in to the [Google Cloud Console](https://console.cloud.google.com/).
2. Use the project selector at the top of the page.
3. Select an existing project, or choose **New project** and create one named
   something recognizable, such as `Drive to Asana`.
4. Make sure that project remains selected for all the following steps.

#### Enable the Google Drive API

1. Open the
   [Google Drive API page](https://console.cloud.google.com/apis/library/drive.googleapis.com).
2. Confirm that the correct Cloud project is selected.
3. Click **Enable**. If the button says **Manage**, the API is already enabled.

#### Configure the Google Auth consent screen

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

#### Create and download the desktop OAuth client

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

### 2. Get the Asana personal access token

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

### 3. Run the tool

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

### Changing or reconnecting credentials

Replace the stored Asana PAT at any time:

```powershell
.venv\Scripts\python.exe drive_to_asana.py auth-asana
```

If the replacement token belongs to a different Asana account, tasks created by
the old account might not be visible to it. On the next sync, the tool creates a
replacement task for an inaccessible prior task and updates its local sync record.

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

### Duplicate behavior

After successfully creating a task, the tool records the Drive file ID and
Asana task ID locally. A later run to the same Asana destination skips that
Drive file, even if the file was renamed. This prevents accidental duplicate
tasks.

If the wizard says everything has already been synced, no new tasks are needed.
Advanced command-line users can bypass the check with `--allow-duplicates`.

### Troubleshooting

#### Google says the app is blocked or access is denied

- For an External app in Testing, confirm that your Google account appears in
  **Google Auth Platform > Audience > Test users**.
- Confirm that the Drive API is enabled in the same project that owns the OAuth
  client.
- Confirm that `drive.metadata.readonly` is listed under **Data Access**.
- A Google Workspace administrator can block restricted OAuth scopes. If the
  settings above are correct, ask the administrator to allow the OAuth client or
  create the Cloud project inside the organization and use an Internal audience.

#### Google authorization worked before but now fails

External Testing refresh tokens can expire after seven days. Run the Google
reconnection command from the previous section and authorize again.

#### `credentials.json` cannot be found

Confirm that the file has that exact name and is in the same directory as
`run_drive_to_asana.bat`. Windows can hide extensions, so make sure the actual
name is not `credentials.json.json`.

#### The Asana token is rejected

Create a replacement PAT in the Asana developer console, then run the Asana
replacement command above. Also confirm that an `ASANA_ACCESS_TOKEN` environment
variable is not overriding the stored token.

#### A workspace or project is missing

The Asana PAT only sees workspaces and projects available to the user who
created it. Add that user to the required workspace/project or create the PAT
from the correct Asana account.

### Optional command-line usage

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

### Manual Python installation

The Windows launcher performs this setup automatically. For manual setup or
other operating systems:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -v
```
