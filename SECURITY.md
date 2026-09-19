# Security Policy

## Trust model — read this before you connect the server

This MCP server gives a language model **file-system reach and process execution
on your machine**. That is the point of the project, and it is also the main
thing to be aware of:

| Capability | What it means in practice |
|---|---|
| `find_projects`, `read_project`, `inspect_point_cloud` | Directories you name are walked and files read |
| `import_scans`, `cancel_job` | `decap.exe` is launched with arguments the model composed, and runs in the background after the tool returns |
| `crop_point_cloud`, `voxel_downsample`, `extract_project_preview` | New files are written to paths the model chooses |
| `extract_cross_section`, `compare_to_bim_mesh`, `export`-style tools | CSV output is written to paths the model chooses |
| `open_project_in_recap` | The ReCap GUI is launched on your desktop |

There is **no sandbox and no confirmation prompt inside the server**. Every guard
rail lives in your MCP client. So:

- **Only connect this server to an MCP client you trust**, and keep that client's
  tool-approval prompts on rather than blanket-approving everything.
- **Nothing here overwrites a source scan in place** — the processing tools write
  new files. But a chosen output path can still land on top of an existing file,
  so prefer a dedicated output directory.
- **Survey data is often personal or commercially sensitive.** A scan of a site
  can contain faces, number plates, and the interior of buildings; a project
  manifest carries file paths, and sometimes the account name of whoever
  registered the scans. Think about that before pointing the model at a folder,
  and before pasting tool output into a shared conversation.
- Treat scan metadata as untrusted input if it came from outside: names, notes
  and measurement labels inside an `.rcp` are free text that the model will read,
  and can carry prompt-injection payloads. The sample project shipped by Autodesk
  contains a note with a URL in it — that is how ordinary this is.

The server opens no network port and sends your data nowhere. `decap.exe` is
Autodesk's own executable, run locally.

## A note on screenshots

If you are preparing images for a public issue or a pull request, read the rules
at the end of [docs/images/README.md](docs/images/README.md). A capture of a live
ReCap window can contain the signed-in Autodesk account name, a real project
name, and site coordinates in the status bar — none of which is the subject of
the image, and all of which is permanent once committed.

## Reporting a vulnerability

Please report privately rather than in a public issue:

- Use GitHub's **[Report a vulnerability](https://github.com/xuantinhnbs-rgb/recap-mcp/security/advisories/new)**
  form on this repository.

Include what an attacker would gain and how to reproduce it. You will get an
acknowledgement, and credit in the fix unless you prefer otherwise.

### What is in scope

- A tool that writes or executes outside what its arguments describe.
- Argument handling that lets crafted input reach a shell or `decap.exe` as
  something other than a path.
- Anything that sends local data off the machine.

### What is not

- The model being able to read and write files. That is the documented purpose of
  the server; the control for it is your MCP client's approval prompts.
- Autodesk ReCap's own behaviour. Report that to Autodesk.
