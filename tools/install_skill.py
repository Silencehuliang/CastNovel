"""把 CastNovel 技能安装到本机的 Agent 技能目录(WorkBuddy / CodeBuddy / Claude 等)。

用法:
  python tools/install_skill.py            # 自动安装到所有检测到的技能目录
  python tools/install_skill.py --dir PATH # 安装到指定目录(其下会建 castnovel/)
  python tools/install_skill.py --remove   # 从检测到的目录移除

技能源文件在仓库 skills/castnovel/(SKILL.md + references/)。
CodeBuddy 读取 ~/.codebuddy/skills/,WorkBuddy 同源技能体系;装完重启对应应用生效。
"""
import argparse
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "skills" / "castnovel"
NAME = "castnovel"

# 常见技能目录:存在即视为候选(后缀 ~/.workbuddy 也算 —— WorkBuddy 与 CodeBuddy 同源)
CANDIDATES = [
    Path.home() / ".codebuddy" / "skills",
    Path.home() / ".workbuddy" / "skills",
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
]


def targets(explicit):
    if explicit:
        return [Path(explicit).expanduser()]
    found = [d for d in CANDIDATES if d.parent.exists()]
    return found or []


def install(dst_dir: Path) -> Path:
    dst = dst_dir / NAME
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(SRC, dst)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="安装到指定目录")
    ap.add_argument("--remove", action="store_true", help="移除已安装的技能")
    args = ap.parse_args()

    if not SRC.exists():
        print(f"找不到技能源:{SRC}", file=sys.stderr)
        sys.exit(1)

    dirs = targets(args.dir)
    if not dirs:
        print("未检测到任何技能目录;用 --dir 指定一个,例如:--dir ~/.workbuddy/skills")
        sys.exit(2)

    for d in dirs:
        dst = d / NAME
        if args.remove:
            if dst.exists():
                shutil.rmtree(dst)
                print(f"已移除:{dst}")
            else:
                print(f"(不存在,跳过){dst}")
        else:
            try:
                d.mkdir(parents=True, exist_ok=True)
                print(f"已安装:{install(d)}")
            except Exception as e:
                print(f"安装失败 {d}:{e}", file=sys.stderr)

    if not args.remove:
        print("\n提示:重启 WorkBuddy / CodeBuddy 后,对话里说「写小说 / 续写第N章 / 导出作品」即会触发 castnovel 技能。")


if __name__ == "__main__":
    main()
