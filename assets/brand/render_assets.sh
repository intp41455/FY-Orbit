#!/usr/bin/env bash
# =====================================================================
# FY 品牌基础资产 —— 一键全套渲染（零成本 / 本机离线）
#
# 用法：
#   bash render_assets.sh
#
# 行为：
#   1. 优先使用托管 venv 里的 Python + Pillow
#   2. 若 venv 缺失则回落到托管基础 Python
#   3. 渲染 favicon 全套（PNG + ICO）与两张 OG 图，并打印实测尺寸 / 字节数
#      / 色板审计 / 版式校验结果
#
# 不会联网、不会安装任何依赖、不会写入 brand-assets/ 以外的任何路径。
# =====================================================================
set -euo pipefail

cd "$(dirname "$0")"

PY_VENV="C:/Users/intpj/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
PY_BASE="C:/Users/intpj/.workbuddy/binaries/python/versions/3.13.12/python.exe"

if [ -x "$PY_VENV" ]; then
  PY="$PY_VENV"
elif [ -x "$PY_BASE" ]; then
  PY="$PY_BASE"
else
  echo "[FATAL] 未找到托管 Python。期望路径：" >&2
  echo "        $PY_VENV" >&2
  echo "        $PY_BASE" >&2
  exit 1
fi

echo "使用 Python: $PY"
"$PY" -c "import PIL; print('Pillow 版本:', PIL.__version__)"
echo
"$PY" render_assets.py
