"""按模型顺序回放的离线 LLM 替身；不改变 llm() 上下文管理器的协议。"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path


class MockClient:
    def __init__(self, responses_path: str | Path | None = None) -> None:
        source = (
            Path(responses_path) if responses_path is not None
            else files("agenttrace").joinpath("data/mock_responses.jsonl")
        )
        self._records: dict[str, list[dict]] = defaultdict(list)
        self._positions: dict[str, int] = defaultdict(int)
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                self._records[record["model"]].append(record)

    def complete(self, model: str, prompt: str) -> dict:
        """prompt 仅用于业务和记录，回放不假装是真实推理。"""
        position = self._positions[model]
        records = self._records.get(model, [])
        if position >= len(records):
            raise LookupError(f"Mock responses exhausted for model {model!r}")
        self._positions[model] += 1
        return dict(records[position])

    @staticmethod
    def stream(response: dict, chunk_size: int = 8) -> Iterator[str]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        output = response["output"]
        for offset in range(0, len(output), chunk_size):
            yield output[offset:offset + chunk_size]
