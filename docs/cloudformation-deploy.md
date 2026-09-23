# Deploying the MCP Server via CloudFormation

This is the automated path: `cloudformation/mcp-server-ec2.yaml` launches the
EC2 instance, security group, and IAM role, and its UserData installs Python,
the MCP SDK, and `server.py`, then starts it as a systemd service — no manual
SSH steps required. For the manual, step-by-step version of the same setup
(useful for understanding *why* each piece exists), see
[`mcp-server-on-ec2-demo.md`](mcp-server-on-ec2-demo.md).

---

## 1. What the stack creates

| Resource | Purpose |
|---|---|
| `McpInstanceRole` / `McpInstanceProfile` | Read-only IAM role (`sts:GetCallerIdentity`, `s3:List*`, `ec2:Describe*`) attached to the instance. No credentials are stored anywhere — the server gets them from IMDS. |
| `McpSecurityGroup` | Opens SSH (22) and the MCP port (8000), each restricted to a CIDR **you** supply. |
| `McpInstance` | Amazon Linux 2023, `t3.micro` by default. UserData installs everything and enables `demo-mcp.service`. |

The `run_command` (arbitrary shell) tool from the manual guide is **not**
installed by this template — see [§8 of the main guide](mcp-server-on-ec2-demo.md#8-security--read-before-enabling-run_command)
before adding it yourself.

---

## 2. Prerequisites

You need, in the target region:

- An existing **EC2 key pair** name (`aws ec2 describe-key-pairs`).
- A **VPC id** and a **public subnet id** in that VPC — a subnet either has
  `MapPublicIpOnLaunch: true` already, or you pass `MapPublicIp=true` to
  force a public IP on the instance's network interface.
- **Your current public IP**, for the security group CIDRs. Don't use
  `0.0.0.0/0` — this server has no authentication.
- AWS CLI v2 configured with a profile that can create EC2 instances, security
  groups, and IAM roles (`--capabilities CAPABILITY_IAM` is required because
  the stack creates an IAM role).

Look these up rather than guessing:

```bash
aws ec2 describe-key-pairs --region <region> \
  --query "KeyPairs[].KeyName" --output table

aws ec2 describe-subnets --region <region> \
  --query "Subnets[].{Id:SubnetId,Vpc:VpcId,AZ:AvailabilityZone,MapPublicIp:MapPublicIpOnLaunch}" \
  --output table

curl -s https://checkip.amazonaws.com   # your current public IP
```

---

## 3. Deploy

```bash
aws cloudformation deploy \
  --region <region> \
  --template-file cloudformation/mcp-server-ec2.yaml \
  --stack-name demo-mcp-server \
  --parameter-overrides \
      KeyName=<your-key-pair> \
      VpcId=<vpc-id> \
      SubnetId=<public-subnet-id> \
      SSHLocationCidr=<your-ip>/32 \
      McpLocationCidr=<your-ip>/32 \
      MapPublicIp=true \
  --capabilities CAPABILITY_IAM
```

> **`MapPublicIp` gotcha:** the instance's `NetworkInterfaces` block sets
> `AssociatePublicIpAddress` explicitly. Setting it to `false` **overrides**
> the subnet's own auto-assign setting rather than deferring to it — the
> instance gets no public IP at all, and the stack fails later when the
> `PublicIp` output tries to read an attribute that doesn't exist. If you
> want a public IP (you almost certainly do, to reach the endpoint from a
> laptop), pass `MapPublicIp=true` regardless of what the subnet's own
> default is.

If `aws` fails on Windows with something like
`'cp932' codec can't decode byte ... illegal multibyte sequence`, that's a
console-encoding issue reading non-ASCII characters (e.g. em dashes) from the
template file, not an AWS error — keep the template's text ASCII-only, or run
the command from a UTF-8 shell.

Deploy takes roughly a minute for the stack, plus 1-2 more minutes for
UserData to install packages and start the service.

---

## 4. Get the outputs

```bash
aws cloudformation describe-stacks --stack-name demo-mcp-server --region <region> \
  --query "Stacks[0].Outputs" --output table
```

Returns `InstanceId`, `PublicIp`, `McpEndpoint`, and a ready-to-run
`RegisterCommand`.

---

## 5. Test the connection

**Wait for the service to start.** Right after the stack finishes, the
instance is often still running `dnf install` / `pip install`; a connection
attempt at that point returns `Connection refused`, not a timeout. Poll until
you get an actual HTTP response:

```bash
for i in $(seq 1 15); do
  code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://<PUBLIC_IP>:8000/mcp)
  echo "attempt $i: http_code=$code"
  [ "$code" != "000" ] && [ -n "$code" ] && break
  sleep 20
done
```

**Confirm it speaks MCP** (expect a JSON-RPC error about a missing session ID
— that's success, see the main guide §7 for why):

```bash
curl -s -X POST http://<PUBLIC_IP>:8000/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Expected:

```json
{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Bad Request: Missing session ID"}}
```

**Register with Claude Code** using the stack's `RegisterCommand` output:

```powershell
claude mcp add --transport http ec2demo http://<PUBLIC_IP>:8000/mcp
claude mcp list   # expect: ec2demo ✔ Connected
```

If you're re-pointing an existing `ec2demo` entry at a new instance (e.g.
after redeploying the stack), remove and re-add it, then reconnect any
already-running Claude Code session with `/mcp`:

```powershell
claude mcp remove ec2demo
claude mcp add --transport http ec2demo http://<PUBLIC_IP>:8000/mcp
```

A session that had the old server connected won't see the new one until you
run `/mcp` to reconnect — `claude mcp add`/`remove` only rewrite the config
file.

**Call a tool** to confirm the full chain (Claude Code → HTTP → instance →
IMDS → IAM role) works, e.g. ask for `whoami` or `disk_usage`. `whoami`
should return the stack's instance-role ARN
(`assumed-role/demo-mcp-server-McpInstanceRole-.../<instance-id>`), not your
own AWS identity — that's the point of running tools through the instance
role instead of local credentials.

---

## 6. Redeploying / updating

`aws cloudformation deploy` is idempotent — re-running it with the same
stack name applies only the diff. If a previous attempt failed and the stack
is stuck in `ROLLBACK_COMPLETE`, that's a terminal state for updates; delete
it first:

```bash
aws cloudformation delete-stack --stack-name demo-mcp-server --region <region>
aws cloudformation wait stack-delete-complete --stack-name demo-mcp-server --region <region>
```

Then deploy again.

---

## 7. Tear down

```bash
aws cloudformation delete-stack --stack-name demo-mcp-server --region <region>
```

This removes the instance, security group, and IAM role together — nothing
is left behind to keep paying for or to leave a stray open port.
