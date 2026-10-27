# Connecting Claude to the website's SEO abilities

How to point Claude Code at `www.sapphirefountains.com` over MCP, so it can run the read-only
`sapphire-seo/*` abilities. Install those first: [README.md](README.md).

**Never put a real application password in this repository, an issue, a PR or a chat. The repo is
public.** Every value below in `<angle brackets>` is a placeholder.

---

## Before you connect: what the AI will be able to do

The `seo-agent` user is an Editor. Through MCP the AI can run every ability that lists itself on
the server and that an Editor may run. That covers:

- **our three:** read-only;
- **core's site, user and environment info:** read-only;
- **Slim SEO's own abilities, including `slim-seo/update-post-meta-tags`.** These can change any
  page's title, description, canonical and noindex on the live site, with no approval step. See
  [Read this first](README.md#read-this-first-slim-seo-already-lets-an-ai-write).

Until Nik decides how to close that, **phase 1 is read-only by instruction.** Tell Claude not to
run any `update-*` ability. Copy suggested changes to Marketing for approval instead.

---

## 1. The `seo-agent` user

The AI signs in as its own WordPress user, **`seo-agent`**, never as a person. That way its access
can be revoked without touching anyone's login, and anything it does is labelled as the AI's.

- **Role: Editor.** Our abilities need `edit_posts`, which Editors have. Don't make it an
  Administrator: that would also unlock Slim SEO's site-wide update abilities and WooCommerce's
  product and order write abilities.
- **Sign-in: an application password, one per person and machine.** In wp-admin, go to **Users →
  seo-agent → Application Passwords**. Name it after who and where, e.g. `Claude Code - Nik -
  desktop`, and choose **Add New Application Password**.
- **WordPress shows the password once,** as six groups of four characters. WordPress ignores the
  spaces, so you can paste it with or without them.

Nik created the user and connected Claude Code this way on 2026-10-27.

---

## 2. Add the server to Claude Code (Windows)

In a terminal:

```bash
claude mcp add sapphire-wordpress --scope user -e WP_API_URL=https://www.sapphirefountains.com/wp-json/mcp/mcp-adapter-default-server -e WP_API_USERNAME=seo-agent -e WP_API_PASSWORD="<application password>" -- cmd /c npx -y "@automattic/mcp-wordpress-remote@latest"
```

- **The server name comes first.** Everything after `--` is the command Claude Code runs.
- **`cmd /c` is needed on Windows.** `npx` is a `.cmd` script there, and Claude Code can't start it
  directly. On macOS or Linux, drop `cmd /c`.
- **`WP_API_URL` is the full MCP endpoint,** not just the site address.
- **The proxy is Automattic's
  [`@automattic/mcp-wordpress-remote`](https://github.com/Automattic/mcp-wordpress-remote).** It
  turns Claude's MCP traffic into signed requests to that endpoint.
- **`--scope user` stores this in your own `%USERPROFILE%\.claude.json`,** in plain text, for every
  project on this machine. Never use `--scope project`: that writes a `.mcp.json` into the repo you
  are standing in.
- **For staging,** add a second server with its own name and the staging host, e.g.
  `sapphire-wordpress-staging` with
  `WP_API_URL=https://<staging host>/wp-json/mcp/mcp-adapter-default-server`.

---

## 3. Check it works

```bash
claude mcp list
```

You should see `sapphire-wordpress: cmd /c npx -y @automattic/mcp-wordpress-remote@latest - ✓
Connected`. Inside a Claude Code session, `/mcp` lists its three tools:

| Tool | What it does |
|---|---|
| `mcp-adapter-discover-abilities` | Lists every ability the server offers |
| `mcp-adapter-get-ability-info` | Shows one ability's input and output schema |
| `mcp-adapter-execute-ability` | Runs one |

`claude mcp get sapphire-wordpress` prints the password. Don't paste its output anywhere.

**If it shows ✗ Failed:**

- **401** means the user name or password is wrong, or the password was revoked.
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
4. *"Run audit-content for post_type product, flagged only, 100 per page. Which products have no
   description of their own? Draft one for each, 50–160 characters."*
5. *"For our ten most important pages, which images are missing alt text? Suggest alt text for each
   one, describing what the photo shows."*

Add this to any prompt, until the Slim SEO question is settled: *"This is read-only. Do not run
any ability whose name contains update, create or delete."*

---

## 5. Revoke access

| To | Do this |
|---|---|
| Cut off one machine | wp-admin → **Users → seo-agent → Application Passwords** → **Revoke** on that row. It takes effect on the next request. |
| Cut off every AI client at once | **Revoke all application passwords** on the same screen. Or change `seo-agent`'s role to *No role for this site*. |
| Remove it from your machine | `claude mcp remove sapphire-wordpress --scope user` |
| A password leaked | Revoke that row first, then make a new one. Nobody needs to change their own login. |

Removing the mu-plugin removes only the three `sapphire-seo` abilities. Slim SEO's, WooCommerce's
and core's abilities stay on the server for as long as `seo-agent` can sign in.
