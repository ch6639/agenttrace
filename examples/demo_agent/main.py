"""python -m examples.demo_agent.main [--stream] [--fail-notes] [--real]"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import agenttrace as at
from agenttrace.mock import MockClient
from examples.demo_agent.business import CourseTutor, collect_response


def call_llm(
    prompt: str, *, mock: bool, stream: bool, client, model: str,
    max_tokens: int | None = None,
) -> Iterator[dict]:
    """业务适配点：统一返回增量/usage 事件，Mock 全程不访问网络。"""
    if mock:
        response = client.complete(model, prompt)
        chunks = client.stream(response) if stream else [response["output"]]
        for chunk in chunks:
            yield {"delta": chunk}
        yield {"usage": {"prompt_tokens": response["token_in"],
                         "completion_tokens": response["token_out"]}}
        return
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("真实模式需要 OPENAI_API_KEY；离线演示请使用默认 Mock 模式")
    if not model:
        raise ValueError("真实模式需要 OPENAI_MODEL，填写所用兼容 API 的模型名")
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "stream": stream}
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    # DeepSeek 默认开启思考；本示例只验收最终文本，短输出预算需显式关闭。
    if urlsplit(base_url).hostname == "api.deepseek.com":
        payload["thinking"] = {"type": "disabled"}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if stream:
        payload["stream_options"] = {"include_usage": True}
    request = Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        if not stream:
            data = json.loads(response.read())
            yield {"delta": data["choices"][0]["message"]["content"] or ""}
            yield {"usage": data.get("usage") or {}}
            return
        completed = False
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            text = line[5:].strip()
            if text == "[DONE]":
                completed = True
                break
            if not text:
                continue
            data = json.loads(text)
            if data.get("error"):
                raise RuntimeError(str(data["error"]))
            for choice in data.get("choices", []):
                yield {"delta": choice.get("delta", {}).get("content") or ""}
                if choice.get("finish_reason") is not None:
                    completed = True
            if data.get("usage"):
                yield {"usage": data["usage"]}
        if not completed:
            raise RuntimeError("LLM 流在完成信号前中断")


def ask_llm(
    prompt: str, *, mock: bool, stream: bool, client: MockClient | None,
    max_tokens: int | None = None,
) -> str:
    model = "mock-tutor" if mock else os.environ.get("OPENAI_MODEL", "")
    # 观测只在统一 LLM 助手处包裹，业务调用不依赖 SDK handle。
    with at.llm(model=model, input=prompt, is_stream=stream) as gen:
        def update(output, usage):
            gen.update(output=output, token_in=usage.get("prompt_tokens"),
                       token_out=usage.get("completion_tokens"))
        return collect_response(
            call_llm(prompt, mock=mock, stream=stream, client=client, model=model,
                     max_tokens=max_tokens), update,
        )


def ask_llm_unobserved(prompt: str, *, mock: bool, stream: bool, client) -> str:
    model = "mock-tutor" if mock else os.environ.get("OPENAI_MODEL", "")
    return collect_response(call_llm(prompt, mock=mock, stream=stream, client=client, model=model))


def create_tutor(*, observed=True) -> CourseTutor:
    """装饰绑定方法，业务类的源码及方法体均不需要修改。"""
    agent = CourseTutor(ask_llm if observed else ask_llm_unobserved, MockClient,
                        on_route=at.log_step if observed else None)
    if observed:
        agent.answer_course = at.tool()(agent.answer_course)
        agent.organize_notes = at.tool()(agent.organize_notes)
        agent.tutor = at.trace("course_tutor")(agent.tutor)
    return agent


_agent = create_tutor()
answer_course, organize_notes, tutor = _agent.answer_course, _agent.organize_notes, _agent.tutor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="课程学习助手（默认离线 Mock）")
    parser.add_argument("--db", default="./agenttrace.db")
    parser.add_argument("--question", default="软件工程是什么，包含哪些核心活动？")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--fail-notes", action="store_true")
    parser.add_argument("--real", action="store_true", help="使用 OPENAI_* 环境变量配置的 API")
    parser.add_argument("--durability", choices=["durable", "async"], default="durable",
                        help="durable 逐事件确认；async 批量写入，强杀可能丢多条")
    args = parser.parse_args(argv)
    at.init(args.db, durability=args.durability)
    try:
        result = tutor(args.question, mock=not args.real or os.getenv("AGENTTRACE_MOCK") == "1",
                       stream=args.stream, fail_notes=args.fail_notes)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if at.flush() else 2
    except Exception as exc:
        from agenttrace.model import redact_text
        print(redact_text(f"{type(exc).__name__}: {exc}"))
        return 1
    finally:
        at.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
