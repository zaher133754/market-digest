"""Read the authenticated Codex catalog without displaying credentials."""

import json
import subprocess
import threading


def main() -> None:
    process = subprocess.Popen(
        ["codex", "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    timer = threading.Timer(25, process.kill)
    timer.start()

    def send(value: dict) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "market_digest_diagnostics", "version": "1.0"}},
            }
        )
        assert process.stdout is not None
        for line in process.stdout:
            response = json.loads(line)
            if response.get("id") == 1:
                send({"method": "initialized", "params": {}})
                send(
                    {
                        "id": 2,
                        "method": "model/list",
                        "params": {"limit": 100, "includeHidden": False},
                    }
                )
            if response.get("id") == 2:
                print(
                    json.dumps(
                        {
                            "models": [
                                {
                                    "model": m.get("model"),
                                    "efforts": m.get("supportedReasoningEfforts"),
                                }
                                for m in response.get("result", {}).get("data", [])
                            ],
                            "error": bool(response.get("error")),
                        },
                        ensure_ascii=False,
                    )
                )
                return
        raise SystemExit("Catalog unavailable: app-server exited or timed out")
    finally:
        process.kill()
        process.wait()
        timer.cancel()


if __name__ == "__main__":
    main()
