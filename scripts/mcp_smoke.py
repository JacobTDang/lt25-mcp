"""Exercise the MCP server over real stdio, without touching the amp.

    ./scripts/py scripts/mcp_smoke.py

Drives the server as a client would: stdin stays open until every reply has
come back, which a plain `cat file | server` pipeline does not do - the server
shuts down on EOF and the last requests go unanswered.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Client:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [str(ROOT / "scripts" / "mcp-server")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._id = 0

    def _send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method,
                    **({"params": params} if params else {})})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"server closed while waiting for {method}")
            message = json.loads(line)
            if message.get("id") == self._id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message["result"]

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def call_tool(self, tool_name: str, /, **arguments):
        # tool_name is positional-only so a tool argument called `name` -
        # which tune_preset has - does not collide with it.
        result = self.request("tools/call", {"name": tool_name, "arguments": arguments})
        text = result["content"][0]["text"]
        if result.get("isError"):
            # A refused call returns isError with a plain-text message, not a
            # JSON-RPC error and not JSON content.
            raise RuntimeError(text)
        return json.loads(text)

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def main() -> int:
    preset = (ROOT / "tests" / "fixtures" / "clean.json").read_text()
    client = Client()
    try:
        info = client.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "1"},
        })
        client.notify("notifications/initialized")
        print(f"connected to {info['serverInfo']['name']}")

        tools = [t["name"] for t in client.request("tools/list")["tools"]]
        print(f"{len(tools)} tools: {', '.join(tools)}")

        described = client.call_tool("describe_preset", preset_json=preset)
        print(f"describe_preset: {described['amp_label']} "
              f"knobs={described['knobs_on_amp_scale']}")

        guide = client.call_tool("tuning_guide", complaint="too fizzy")
        moves = ", ".join(f"{m['control']}{m['delta_on_0_to_10_scale']:+}"
                          for m in guide["moves"])
        print(f"tuning_guide('too fizzy'): {moves}")

        tuned = client.call_tool(
            "tune_preset", preset_json=preset,
            knobs={"gain": 3.5, "treb": 7.0},
            amp_model="DUBS_Deluxe65", name="COURAGE",
        )
        print(f"tune_preset: {tuned['changes']}")
        print(f"            knobs now {tuned['knobs_on_amp_scale']}")

        prompt = client.request("prompts/get", {
            "name": "match_tone", "arguments": {"target": "the courage solo tone"}})
        first = prompt["messages"][0]["content"]["text"].splitlines()[0]
        print(f"prompt match_tone: {first}")

        try:
            client.call_tool("tune_preset", preset_json=preset, knobs={"gain": 50})
        except RuntimeError:
            print("guard: out-of-range knob refused")
        else:
            print("guard FAILED: out-of-range knob was accepted", file=sys.stderr)
            return 1
    finally:
        client.close()
    print("\nall good - no amp required for any of this")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
