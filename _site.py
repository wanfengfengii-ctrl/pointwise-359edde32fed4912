# -*- coding: utf-8 -*-
"""受信启动器：在 -I 隔离模式下把仓库目录加入 sys.path 后运行目标脚本。

用法：python3 -I _site.py checks.py [args...]
不修改任何冻结生产文件；仓库目录是受信代码所在位置，而非下载数据目录。
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
if len(sys.argv) < 2:
    raise SystemExit(2)
script = sys.argv[1]
if not os.path.isabs(script):
    script = os.path.join(ROOT, script)
sys.argv = [script, *sys.argv[2:]]
runpy.run_path(script, run_name="__main__")
