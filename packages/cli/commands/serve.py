"""
commands/serve.py — 启动 API / Web server

  agent serve api [--host 127.0.0.1] [--port 8000] [--reload]
  agent serve web [--port 3000]       # 未来 Vite/Next.js 静态文件
"""

from __future__ import annotations

from typing import Annotated

import typer

from packages.cli.render import console, print_error, print_info

app = typer.Typer(help="启动 API server 或 Web 服务")


@app.command("api")
def serve_api_cmd(
    host: Annotated[str, typer.Option("--host", help="监听地址（默认 127.0.0.1）")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="监听端口")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", is_flag=True, help="开发模式热重载")] = False,
    workers: Annotated[int, typer.Option("--workers", help="worker 进程数（production 用）")] = 1,
) -> None:
    """
    启动 FastAPI REST + SSE 服务。

    开发模式::

      agent serve api --reload

    生产模式::

      agent serve api --host 0.0.0.0 --port 8000 --workers 2
    """
    try:
        import uvicorn
    except ImportError:
        print_error("uvicorn 未安装，运行: uv add uvicorn[standard]")
        raise typer.Exit(1)

    try:
        import packages.api.fastapi_app  # noqa: F401
    except ImportError:
        print_error(
            "packages/api/fastapi_app.py 尚未创建（P3 阶段），"
            "目前仅支持 CLI 本地模式。"
        )
        raise typer.Exit(1)

    console.print(f"[bold green]Starting API server[/] on http://{host}:{port}")
    if host != "127.0.0.1":
        console.print(
            "[bold yellow]警告:[/] 监听非本地地址，请确认已配置 token 认证。"
        )

    uvicorn.run(
        "packages.api.fastapi_app:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,
        log_level="info",
    )


@app.command("web")
def serve_web_cmd(
    port: Annotated[int, typer.Option("--port", help="前端开发服务器端口")] = 3000,
) -> None:
    """
    启动前端开发服务器（P4 阶段 Vite React）。

    需要先 `cd web && npm install`。
    """
    import subprocess
    import sys
    from pathlib import Path

    web_dir = Path(__file__).parents[4] / "web"
    if not web_dir.exists():
        print_error(
            "web/ 目录不存在（P4 阶段创建），"
            "当前阶段请使用 'agent serve api' 配合浏览器直接调用 API。"
        )
        raise typer.Exit(1)

    console.print(f"[bold green]Starting web dev server[/] on http://localhost:{port}")
    subprocess.run(
        ["npm", "run", "dev", "--", "--port", str(port)],
        cwd=web_dir,
        check=True,
    )
