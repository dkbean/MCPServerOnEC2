import shlex
import subprocess
import boto3
from botocore.exceptions import ClientError
from mcp.server import MCPServer

mcp = MCPServer("ec2-demo")


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
# Arbitrary shell execution. Disabled by default — see docs/mcp-server-on-ec2-demo.md
# section 8 before enabling this on a reachable endpoint.

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
