# Mini Agent Python API SDK

`mini_agent_client.py` 是零配置依赖（仅 `httpx`）的同步客户端，覆盖会话、消息、SSE、附件、导入导出和常用管理接口。

```python
from mini_agent_sdk import MiniAgentClient

with MiniAgentClient("http://127.0.0.1:8000", token="optional-token") as client:
    session = client.create_session(title="SDK demo")
    client.send(session["id"], "请总结当前工作区")
    for event in client.stream_events(session["id"]):
        if event["type"] == "message.completed":
            print(event["data"])
            break
```

- 远端访问需要设置 `MINI_AGENT_API_TOKEN`，并在配置中开启 `api.requireTokenRemote`；本地回环访问默认不要求 Token。
- 完整 HTTP 接口见运行中的 `/docs`，或仓库 `docs/API.md`。
