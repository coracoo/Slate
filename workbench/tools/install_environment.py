# -*- coding: utf-8 -*-
"""按预定义分组安装项目虚拟环境依赖。"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GROUPS = ("base", "depth", "chatgpt")


def normalize_groups(groups):
    if not isinstance(groups, list) or not groups or any(not isinstance(x, str) or x not in GROUPS for x in groups):
        raise ValueError("请选择基础依赖、深度依赖或 ChatGPT 程序")
    return [x for x in GROUPS if x in groups]


def commands(groups, python):
    selected = normalize_groups(groups)
    result = []
    if "base" in selected:
        result.append([python, "-m", "pip", "install", "--require-hashes", "-r", str(ROOT / "requirements-win-py312.lock")])
    if "depth" in selected:
        result.append([python, "-m", "pip", "install", "torch", "torchvision", "transformers", "rembg[cpu]"])
    if "chatgpt" in selected:
        result.append([python, str(ROOT / "workbench/tools/install_image_use.py")])
    result.append([python, "-m", "pip", "check"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("groups", nargs="+", choices=GROUPS)
    args = parser.parse_args()
    python = ROOT / ".venv/Scripts/python.exe"
    if not python.is_file():
        raise SystemExit("缺少项目 .venv，请按 README 使用 Python 3.12 创建环境后重试。")
    result = subprocess.run([str(python), "-c", "import sys; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)"])
    if result.returncode:
        raise SystemExit("项目 .venv 必须使用 Python 3.12，请按 README 重建。")
    for index, cmd in enumerate(commands(args.groups, str(python)), 1):
        print(f"[{index}] 执行安装步骤", flush=True)
        subprocess.run(cmd, cwd=ROOT, check=True)
    print("安装完成。请重新检测环境；基础依赖变更后重新启动前台服务。", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
