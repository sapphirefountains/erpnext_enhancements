# Connecting Claude to the website's SEO abilities

How to point Claude Code at `www.sapphirefountains.com` over MCP, so it can run the read-only
`sapphire-seo/*` abilities. Install those first: [README.md](README.md).

**Never put a real application password in this repository, an issue, a PR or a chat. The repo is
public.** Every value below in `<angle brackets>` is a placeholder.

---

## Before you connect: what the AI will be able to do

The AI signs in as `seo-agent`, which must be a **Contributor**. Through MCP it can run every ability
that lists itself on the server and that a Contributor may run:

- **our three `sapphire-seo` abilities:** read-only;
- **core's user info,** about `seo-agent` itself. Core's site and environment info need
  `manage_options`, so they are refused.

It can't run any of Slim SEO's or WooCommerce's abilities:

- **Slim SEO's update abilities** check `edit_post` and `edit_term`, which a Contributor fails on
  every published page, product and category. Its read abilities check the same.
- **WooCommerce's abilities** need WooCommerce's own capabilities, which a Contributor doesn't have.

**As an Editor, it could rewrite any page's SEO settings live, with no approval.** That is why the
role matters; see
[Read this first](README.md#read-this-first-the-ais-user-must-be-a-contributor-not-an-editor).
The application password also works for the whole REST API, not only MCP. Whatever the role allows,
anyone holding the password can do.

---

## 1. The `seo-agent` user

The AI signs in as its own WordPress user, **`seo-agent`**, never as a person. That way its access
can be revoked without touching anyone's login, and anything it does is labelled as the AI's.

- **Role: Contributor.** Our abilities need `edit_posts`, which a Contributor has, and the MCP
  Adapter needs `read`. Nothing more:
  - Not Editor or Author: an Editor unlocks Slim SEO's update abilities, and an Author can publish.
  - Not Shop Manager or Administrator: those also unlock WooCommerce's write abilities and Slim
    SEO's site-wide ones.
  - A custom role with only `read` and `edit_posts` works too.
- **If `seo-agent` is already an Editor** (it was made one on 2026-10-27): go to wp-admin →
  **Users → seo-agent → Role → Contributor → Update User**. Its application passwords keep
  working, so nobody needs to reconnect.
- **Sign-in: an application password, one per person and machine.** Go to **Users → seo-agent →
  Application Passwords**. Name it after who and where, e.g. `Claude Code - Nik - desktop`, and
  choose **Add New Application Password**.
- **WordPress shows the password once,** as six groups of four characters. WordPress ignores the
  spaces, so you can paste it with or without them.

---

## 2. Add the server to Claude Code (Windows)

The command pins the proxy, Automattic's
[`@automattic/mcp-wordpress-remote`](https://github.com/Automattic/mcp-wordpress-remote), to an
exact version: **0.5.4**, the current release on 2026-10-27, published 2026-10-20. `@latest` would
download whatever is newest each time Claude Code starts, and run it with the password in its
environment.

In **PowerShell 7**, read the password into a variable first, so that it never appears in your
command history:

```powershell
$pw = Read-Host -Prompt "seo-agent application password" -MaskInput
claude mcp add sapphire-wordpress --scope user -e WP_API_URL=https://www.sapphirefountains.com/wp-json/mcp/mcp-adapter-default-server -e WP_API_USERNAME=seo-agent -e "WP_API_PASSWORD=$pw" -- cmd /c npx -y "@automattic/mcp-wordpress-remote@0.5.4"
Remove-Variable pw
```

What the parts mean:

- **The server name comes first.** Everything after `--` is the command Claude Code runs.
- **`cmd /c` is needed on Windows.** `npx` is a `.cmd` script there, and Claude Code can't start it
  directly. On macOS or Linux, drop `cmd /c`.
- **`WP_API_URL` is the full MCP endpoint,** not just the site address.
- **`--scope user` stores this in your own `%USERPROFILE%\.claude.json`,** for every project on
  this machine. The password sits there **in plain text**, so treat that file like a password.
  Never use `--scope project`: that writes a `.mcp.json` into the repo you are standing in.
- **For staging,** add a second server with its own name and the staging host, e.g.
  `sapphire-wordpress-staging` with
  `WP_API_URL=https://<staging host>/wp-json/mcp/mcp-adapter-default-server`.

**If you typed the password into the command itself,** PowerShell saved that line to its history
file. Open the file and delete the line that contains `WP_API_PASSWORD`:

```powershell
notepad (Get-PSReadLineOption).HistorySavePath
```

### Upgrading the proxy on purpose

1. Read the proxy's
   [release notes](https://github.com/Automattic/mcp-wordpress-remote/releases).
2. Check the current version:

   ```bash
   npm view @automattic/mcp-wordpress-remote version
   ```

3. Remove the server and add it again with the new version number:

   ```bash
   claude mcp remove sapphire-wordpress --scope user
   ```

4. Update the version in this guide in the same PR, so everyone pins the same one.

---

## 3. Check it works

```bash
claude mcp list
```

You should see `sapphire-wordpress: cmd /c npx -y @automattic/mcp-wordpress-remote@0.5.4 - ✓
Connected`. Inside a Claude Code session, `/mcp` lists its three tools:

| Tool | What it does |
|---|---|
| `mcp-adapter-discover-abilities` | Lists every ability the server offers |
| `mcp-adapter-get-ability-info` | Shows one ability's input and output schema |
| `mcp-adapter-execute-ability` | Runs one |

`claude mcp get sapphire-wordpress` prints the password. Don't paste its output anywhere.

**If it shows ✗ Failed:**

- **`401 incorrect_password`** means the password is wrong or was revoked.
- **An HTML 403 with a Cloudflare Ray ID** means Cloudflare stopped the request before WordPress
  saw it. See [If Cloudflare blocks it](README.md#if-cloudflare-blocks-it).
- **`npx` not found** means Node.js isn't installed or isn't on the `PATH`.

---

## 4. First prompts

Start with the summary. It is one call, and it tells Claude the rules and thresholds in force.

1. *"Using the sapphire-wordpress tools, discover the abilities in the sapphire-seo category, then
   run sapphire-seo/site-summary. Summarise the biggest SEO problems, worst first. Read the notes in
   the response. Don't change anything."*
2. *"Run sapphire-seo/audit-content with flag duplicate_title, then flag duplicate_description.
   For each group, suggest a distinct title or description for every page. Give it as a table I
   can send to Marketing."*
3. *"Run sapphire-seo/page-detail for /fountain-design/ with live_check true. Compare the stored,
   effective and served title and description. List the headings and the schema types the page
   serves."*
4. *"Run audit-content for post_type product with flag description_missing, 25 per page, and page
   through all of them. Draft a description of 50–160 characters for each."*
5. *"For our ten most important pages, which images are missing alt text? Suggest alt text for each
   one, describing what the photo shows."*

Phase 1 is read-only. Suggestions go to Marketing for approval; nothing is changed on the site
from Claude.

---

## 5. Revoke access

| To | Do this |
|---|---|
| Cut off one machine | wp-admin → **Users → seo-agent → Application Passwords** → **Revoke** on that row. It takes effect on the next request. |
| Cut off every AI client at once | **Revoke all application passwords** on the same screen. Or change `seo-agent`'s role to *No role for this site*. |
| Remove it from your machine | `claude mcp remove sapphire-wordpress --scope user` |
| A password leaked | Revoke that row first, then make a new one. Nobody needs to change their own login. |

Removing the mu-plugin removes only the three `sapphire-seo` abilities. Everything else `seo-agent`
may run stays on the server for as long as it can sign in.
