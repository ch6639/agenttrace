"""独立业务基线：无 AgentTrace 导入，观测版本复用同一份方法实现。"""

from __future__ import annotations

COURSE = "软件工程包括需求分析、设计、实现、测试和维护；强调质量、过程和可追溯性。"


def collect_response(events, on_complete=None) -> str:
    parts, usage = [], {}
    try:
        for event in events:
            if "delta" in event:
                parts.append(event["delta"])
            if "usage" in event:
                usage = event["usage"]
    finally:
        if on_complete is not None:
            on_complete("".join(parts), usage)
    return "".join(parts)


class CourseTutor:
    def __init__(self, ask_llm, client_factory, on_route=None):
        self.ask_llm = ask_llm
        self.client_factory = client_factory
        self.on_route = on_route

    def answer_course(self, question, *, mock, stream, client) -> str:
        return self.ask_llm(f"依据课程资料回答问题。资料：{COURSE}\n问题：{question}",
                            mock=mock, stream=stream, client=client)

    def organize_notes(self, answer, *, mock, stream, client, fail=False) -> str:
        if fail:
            raise RuntimeError("模拟笔记整理工具失败")
        return self.ask_llm(f"将以下答疑整理成结构化学习笔记：\n{answer}",
                            mock=mock, stream=stream, client=client)

    def tutor(self, question, *, mock=True, stream=False, fail_notes=False) -> dict:
        client = self.client_factory() if mock else None
        if self.on_route is not None:
            self.on_route("route", {"tools": ["answer_course", "organize_notes"]})
        answer = self.answer_course(question, mock=mock, stream=stream, client=client)
        notes = self.organize_notes(
            answer, mock=mock, stream=stream, client=client, fail=fail_notes,
        )
        return {"answer": answer, "notes": notes}
