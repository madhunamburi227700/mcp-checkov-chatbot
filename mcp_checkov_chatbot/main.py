import asyncio
import json
import os
import logging
import subprocess
from dotenv import load_dotenv
from mcp.client.stdio import stdio_client
from mcp import ClientSession, StdioServerParameters
import httpx

logging.basicConfig(level=logging.INFO)

REPO_DIR = "terraform-vulnerability-lab"
SRC_DIR = os.path.join(REPO_DIR, "src")

# ========== Terraform Format Check ==========
def check_terraform_fmt(path=SRC_DIR) -> str:
    abs_path = os.path.abspath(path)
    try:
        result = subprocess.run(["terraform", "fmt", "-check", "-recursive"], cwd=abs_path, capture_output=True, text=True)
        return "✅ All Terraform files are correctly formatted." if result.returncode == 0 else f"⚠ Formatting issues:\n{result.stdout.strip()}"
    except Exception as e:
        return f"❌ Error running terraform fmt: {e}"

# ========== Terraform Auto Format ==========
def auto_terraform_fmt(path=SRC_DIR) -> str:
    abs_path = os.path.abspath(path)
    try:
        result = subprocess.run(["terraform", "fmt", "-recursive"], cwd=abs_path, capture_output=True, text=True)
        return result.stdout.strip() or "✅ Auto-format complete. No changes."
    except Exception as e:
        return f"❌ Error running terraform fmt: {e}"

# ========== Terraform Validate ==========
def check_terraform_validate(path=SRC_DIR) -> str:
    abs_path = os.path.abspath(path)
    try:
        subprocess.run(["terraform", "init", "-input=false", "-backend=false"], cwd=abs_path, capture_output=True)
        result = subprocess.run(["terraform", "validate"], cwd=abs_path, capture_output=True, text=True)
        return "✅ Terraform configuration is valid." if result.returncode == 0 else f"❌ Validation failed:\n{result.stdout.strip()}"
    except Exception as e:
        return f"❌ Error running terraform validate: {e}"

# ========== Checkov Scan ==========
def run_checkov_scan(path=SRC_DIR, output_file="report.json"):
    abs_path = os.path.abspath(path)
    try:
        command = [
            "docker", "run", "--rm",
            "-v", f"{abs_path}:/tf",
            "bridgecrew/checkov:latest",
            "-d", "/tf",
            "-o", "json"
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.stdout)
        return f"✅ Checkov scan done. Output: {output_file}"
    except subprocess.SubprocessError as e:
        return f"❌ Checkov error: {e}"

# ========== MCP Server ==========
class Server:
    def __init__(self, name, config):
        self.name = name
        self.config = config
        self.session = None

    async def initialize(self):
        from contextlib import AsyncExitStack
        self.exit_stack = AsyncExitStack()
        await self.exit_stack.__aenter__()
        read, write = await self.exit_stack.enter_async_context(stdio_client(StdioServerParameters(**self.config)))
        self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        logging.info(f"✅ Initialized server: {self.name}")

    async def cleanup(self):
        await self.exit_stack.aclose()

# ========== OpenAI GPT Client ==========
class LLMClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_response(self, messages: list[dict]) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4o",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024
        }
        try:
            response = httpx.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logging.error(f"OpenAI API error: {e}")
            return "❌ GPT Error"

# ========== GitHub PR Creation ==========
def create_git_pr_docker(github_token: str):
    abs_repo = os.path.abspath(REPO_DIR)
    try:
        subprocess.run([
            "docker", "run", "--rm",
            "-v", f"{abs_repo}:/repo",
            "-w", "/repo",
            "-e", f"GITHUB_TOKEN={github_token}",
            "debian:bullseye", "bash", "-c",
            """
            apt update &&
            apt install -y git &&
            git config --global user.name 'mcp-bot' &&
            git config --global user.email 'bot@example.com' &&
            git fetch origin &&
            git checkout -B fix/checkov-patch &&
            git add . &&
            git commit -m 'fix: apply terraform and checkov fixes' || echo 'Nothing to commit' &&
            git push https://${GITHUB_TOKEN}@github.com/madhunamburi227700/terraform-vulnerability-lab.git fix/checkov-patch
            """
        ], check=True)
        return "✅ PR branch pushed to GitHub: fix/checkov-patch"
    except subprocess.CalledProcessError as e:
        return f"❌ Error creating PR: {e}"

# ========== Main ==========
async def main():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    github_token = os.getenv("GITHUB_TOKEN")
    if not api_key or not github_token:
        raise ValueError("❌ Missing OPENAI_API_KEY or GITHUB_TOKEN in .env")

    with open("servers_config.json") as f:
        config = json.load(f)
    if "github" in config["mcpServers"]:
        args = config["mcpServers"]["github"]["args"]
        for i, arg in enumerate(args):
            if arg.startswith("GITHUB_PERSONAL_ACCESS_TOKEN="):
                args[i] = f"GITHUB_PERSONAL_ACCESS_TOKEN={github_token}"

    servers = [Server(name, cfg) for name, cfg in config["mcpServers"].items()]
    for server in servers:
        await server.initialize()

    print("✅ DevSecOps Chatbot Ready")
    print("Commands: fmt | auto_fmt | validate | scan | pr | exit | or free chat\n")

    chat_messages = [{
        "role": "system",
        "content": "You are a DevSecOps assistant. Help with Terraform, Checkov, AWS security best practices, and GitHub PRs."
    }]

    llm = LLMClient(api_key)

    while True:
        user_input = input("💬 You: ").strip().lower()

        if user_input == "exit":
            break

        elif user_input == "fmt":
            print(check_terraform_fmt())

        elif user_input == "auto_fmt":
            print(auto_terraform_fmt())

        elif user_input == "validate":
            print(check_terraform_validate())

        elif user_input == "scan":
            print(run_checkov_scan())
            if os.path.exists("report.json"):
                with open("report.json") as f:
                    try:
                        report = json.load(f)
                    except json.JSONDecodeError:
                        print("❌ Failed to parse Checkov report.")
                        continue

                failed = report.get("results", {}).get("failed_checks", [])
                if failed:
                    print(f"⚠ Found {len(failed)} issues. Suggesting fixes for top 3:")
                    for check in failed[:3]:
                        resource = check.get("resource")
                        check_id = check.get("check_id")
                        check_name = check.get("check_name")
                        file_path = check.get("file_path")
                        code_block = "\n".join(line[1] for line in check.get("code_block", []))

                        prompt = f"""Checkov found a vulnerability:

* Check Name: {check_name} ({check_id})
* Resource: {resource}
* File: {file_path}

Code Block:

{code_block}

👉 Suggest a fix using best practices.
"""
                        chat_messages.append({"role": "user", "content": prompt})
                        reply = llm.get_response(chat_messages)
                        print(f"\n💡 Suggestion for {check_id}:\n{reply}\n")
                        chat_messages.append({"role": "assistant", "content": reply})
                else:
                    print("✅ No failed checks found.")

        elif user_input == "pr":
            print("🚀 Creating GitHub PR using Docker...")
            pr_result = create_git_pr_docker(github_token)
            print(pr_result)

            print("🔍 Re-running Checkov to verify if issues are fixed...")
            scan_result = run_checkov_scan()
            print(scan_result)

            if os.path.exists("report.json"):
                with open("report.json") as f:
                    try:
                        report = json.load(f)
                    except json.JSONDecodeError:
                        print("❌ Failed to parse Checkov report.")
                        continue

                failed = report.get("results", {}).get("failed_checks", [])
                if failed:
                    print(f"⚠ Still found {len(failed)} issues after PR. Further review needed.")
                else:
                    print("✅ All vulnerabilities resolved after PR! 🎉")

        else:
            chat_messages.append({"role": "user", "content": user_input})
            reply = llm.get_response(chat_messages)
            print(f"\n🤖 GPT-4o:\n{reply}\n")
            chat_messages.append({"role": "assistant", "content": reply})

    for server in servers:
        await server.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
