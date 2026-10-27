"""CastNovel(墨铸)启动入口:python main.py 后访问 http://127.0.0.1:8000"""
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import WEB_DIR
from app.routes import router

app = FastAPI(title="CastNovel · AI 长篇小说创作工坊")
db.init()
app.include_router(router, prefix="/api")
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"\n  CastNovel 已启动: http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
