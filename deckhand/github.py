from __future__ import annotations

import subprocess
from collections.abc import Callable

from deckhand.step import Refusal

Runner = Callable[[list[str]], str]


def run_gh(args: list[str]) -> str:
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=60)
    except FileNotFoundError:
        raise Refusal("the gh command is not installed") from None
    except subprocess.TimeoutExpired:
        raise Refusal("gh did not answer in time") from None
    except subprocess.CalledProcessError as error:
        raise Refusal(f"gh {' '.join(args[:3])} failed: {error.stderr.strip()}") from None
    return done.stdout


class Github:
    def __init__(self, runner: Runner = run_gh) -> None:
        self._run = runner

    def auto_merge_allowed(self, repository: str) -> bool:
        out = self._run(["api", f"repos/{repository}", "--jq", ".allow_auto_merge"])
        return out.strip() == "true"

    def allow_auto_merge(self, repository: str) -> None:
        self._run(["api", "-X", "PATCH", f"repos/{repository}", "-F", "allow_auto_merge=true"])
