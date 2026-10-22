"""TC-07 子进程：在提交前、事务中、提交后等待父进程强杀。"""

import sys
import time
from pathlib import Path

import agenttrace as at
from agenttrace import api
from agenttrace.storage import _UPSERT_SPAN

db, marker, phase = sys.argv[1:]
at.init(db)  # 零配置默认必须满足持久性要求，不依赖 flush/正常退出。
with at.session("crash-test"):
    with at.llm(model="mock", input="question") as gen:
        gen.update(output="answer", token_in=1, token_out=2)
    for i in range(20):
        at.log_step(f"acknowledged-{i}")
    storage = api.get_runtime().storage
    original = storage.write_batch

    def pause(traces, spans):
        if not any(s["name"] == "in-flight" for s in spans):
            return original(traces, spans)
        if phase == "after":
            original(traces, spans)
        elif phase == "during":
            storage._conn.execute("BEGIN")
            storage._conn.execute(_UPSERT_SPAN, next(s for s in spans if s["name"] == "in-flight"))
        Path(marker).write_text("ready", encoding="utf-8")
        while True:
            time.sleep(1)

    storage.write_batch = pause
    at.log_step("in-flight")
