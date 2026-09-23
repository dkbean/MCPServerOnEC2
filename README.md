# MCP Server on EC2 — Demo

A Model Context Protocol (MCP) server that runs on EC2 and exposes read-only
host and AWS tools (disk usage, top processes, S3/EC2/security-group
listings) over streamable HTTP, so Claude Code on a laptop can call them
remotely using the instance's IAM role — no credentials stored locally or on
the instance.

Full write-up, architecture, troubleshooting, and security notes:
[`docs/mcp-server-on-ec2-demo.md`](docs/mcp-server-on-ec2-demo.md) (manual,
step-by-step setup) and
[`docs/cloudformation-deploy.md`](docs/cloudformation-deploy.md) (automated
deploy with this repo's CloudFormation stack).

## Layout

```
cloudformation/mcp-server-ec2.yaml     CloudFormation stack: EC2 + SG + IAM role,
                                        self-configuring via UserData
src/server.py                          Reference copy of the MCP server (same code
                                        the stack's UserData installs), for local dev
docs/mcp-server-on-ec2-demo.md         Full manual step-by-step guide
docs/cloudformation-deploy.md          Deploy/test/teardown guide for the stack below
```

## Deploy

Requires an existing VPC, a public subnet, and an EC2 key pair. Nothing
secret is stored in the template — supply your own values at deploy time.
See [`docs/cloudformation-deploy.md`](docs/cloudformation-deploy.md) for how
to look those up, the full testing procedure, and the `MapPublicIp` gotcha.

```bash
aws cloudformation deploy \
  --template-file cloudformation/mcp-server-ec2.yaml \
  --stack-name demo-mcp-server \
  --parameter-overrides \
      KeyName=<your-key-pair> \
      VpcId=<vpc-id> \
      SubnetId=<public-subnet-id> \
      SSHLocationCidr=<your-ip>/32 \
      McpLocationCidr=<your-ip>/32 \
  --capabilities CAPABILITY_IAM
```

Then, once the instance finishes booting (~2 minutes):

```bash
aws cloudformation describe-stacks --stack-name demo-mcp-server \
  --query "Stacks[0].Outputs"
```

Use the `RegisterCommand` output to add the server to Claude Code.

## Security notes

- SSH and the MCP port are restricted to CIDRs **you** supply — never leave
  these as `0.0.0.0/0`.
- The instance role is scoped to read-only actions
  (`sts:GetCallerIdentity`, `s3:List*`, `ec2:Describe*`) — no write/delete
  permissions.
- The `run_command` (arbitrary shell) tool from the guide is intentionally
  **not** installed by this template's UserData. Add it yourself only after
  reading section 8 of the guide, and prefer an SSH tunnel over exposing the
  port publicly.
- Nothing in this repo contains real IPs, key names, account IDs, or other
  personal/account-identifying data — all examples use placeholders.

## Local development

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install "mcp[cli]>=2,<3" boto3
python src/server.py
```
