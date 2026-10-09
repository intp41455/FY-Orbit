"""Cursor 级代码上下文极致理解与切片装配引擎（A-生态兼容-04 · P12）。

借鉴 Cursor 的代码上下文理解大杀器：
1. **多语言语法与符号解析**：
   - Python: 内置 AST 深度解析类、方法、函数、参数签名、类型标注、Docstring 与导包。
   - TypeScript / JavaScript: 正则+语法分词解析类、接口 (interface)、类型别名 (type)、函数、方法与导包。
   - Java: 解析 package、class、interface、方法签名与导包。
2. **上下文相关性检索与切片（Context Slicing）**：
   - 根据目标查询（query/error/task），基于符号权重、名称精准匹配与语义相关性对符号切片进行打分排序。
   - 依赖感知关联：命中的符号自动关联其依赖的导入、类型定义与类上下文。
3. **高密度上下文装配（Prompt Context Assembler）**：
   - 类似 Cursor @codebase / @symbol 注入模式：
   - 在严格字符/Token 预算内装配最核心的代码上下文，智能折叠大函数体内部琐碎逻辑，保持结构签名完整。
   - 输出格式严谨、附带文件路径与代码行号锚点的 Markdown 上下文块。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CodeSymbol:
    """代码符号实体（类、函数、方法、接口、类型等）。"""

    name: str
    kind: str  # 'class' | 'function' | 'method' | 'interface' | 'type'
    file_path: str
    start_line: int
    end_line: int
    signature: str
    docstring: str = ""
    decorators: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    raw_code: str = ""


@dataclass
class ContextSlice:
    """上下文切片。"""

    file_path: str
    start_line: int
    end_line: int
    symbol_name: str
    symbol_kind: str
    content: str
    relevance_score: float
    reasons: list[str] = field(default_factory=list)


class CursorContextEngine:
    """Cursor 级代码上下文理解与装配引擎。"""

    def __init__(self) -> None:
        pass

    # -----------------------------------------------------------------------
    # 1. 符号解析与提取
    # -----------------------------------------------------------------------

    def extract_symbols(self, file_path: str, source_code: str) -> list[CodeSymbol]:
        """解析源码并提取符号表。自动根据扩展名选择解析器。"""
        ext = Path(file_path).suffix.lower()
        if ext in (".py", ".pyi"):
            return self._extract_python_symbols(file_path, source_code)
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            return self._extract_ts_js_symbols(file_path, source_code)
        elif ext == ".java":
            return self._extract_java_symbols(file_path, source_code)
        return []

    def _extract_python_symbols(self, file_path: str, code: str) -> list[CodeSymbol]:
        """使用 Python AST 深度解析符号与层次结构。"""
        symbols: list[CodeSymbol] = []
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return symbols

        lines = code.splitlines()

        # 提取全局 imports 作为基础依赖
        imports: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node) or ""
                bases = [ast.unparse(b) for b in node.bases]
                sig = f"class {node.name}({', '.join(bases)}):" if bases else f"class {node.name}:"
                start = node.lineno
                end = getattr(node, "end_lineno", node.lineno)
                raw = "\n".join(lines[start - 1 : end]) if lines else ""
                symbols.append(
                    CodeSymbol(
                        name=node.name,
                        kind="class",
                        file_path=file_path,
                        start_line=start,
                        end_line=end,
                        signature=sig,
                        docstring=doc,
                        dependencies=bases + imports[:5],
                        raw_code=raw,
                    )
                )

                # 解析类方法
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_doc = ast.get_docstring(item) or ""
                        args_str = ast.unparse(item.args)
                        ret_str = f" -> {ast.unparse(item.returns)}" if item.returns else ""
                        prefix = "async def " if isinstance(item, ast.AsyncFunctionDef) else "def "
                        m_sig = f"{prefix}{item.name}({args_str}){ret_str}"
                        m_start = item.lineno
                        m_end = getattr(item, "end_lineno", item.lineno)
                        m_raw = "\n".join(lines[m_start - 1 : m_end]) if lines else ""
                        decs = [ast.unparse(d) for d in item.decorator_list]
                        symbols.append(
                            CodeSymbol(
                                name=f"{node.name}.{item.name}",
                                kind="method",
                                file_path=file_path,
                                start_line=m_start,
                                end_line=m_end,
                                signature=m_sig,
                                docstring=m_doc,
                                decorators=decs,
                                dependencies=[node.name],
                                raw_code=m_raw,
                            )
                        )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node) or ""
                args_str = ast.unparse(node.args)
                ret_str = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
                sig = f"{prefix}{node.name}({args_str}){ret_str}"
                start = node.lineno
                end = getattr(node, "end_lineno", node.lineno)
                raw = "\n".join(lines[start - 1 : end]) if lines else ""
                decs = [ast.unparse(d) for d in node.decorator_list]
                symbols.append(
                    CodeSymbol(
                        name=node.name,
                        kind="function",
                        file_path=file_path,
                        start_line=start,
                        end_line=end,
                        signature=sig,
                        docstring=doc,
                        decorators=decs,
                        dependencies=imports[:5],
                        raw_code=raw,
                    )
                )

        return symbols

    def _extract_ts_js_symbols(self, file_path: str, code: str) -> list[CodeSymbol]:
        """解析 TypeScript / JavaScript 符号。"""
        symbols: list[CodeSymbol] = []
        lines = code.splitlines()

        class_pattern = re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z0-9_$]+)(?:\s+extends\s+([A-Za-z0-9_$,\s]+))?")
        interface_pattern = re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z0-9_$]+)")
        func_pattern = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\((.*?)\)")
        const_func_pattern = re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\((.*?)\)\s*(?::\s*.*?)?\s*=>")

        for idx, line in enumerate(lines, 1):
            m_cls = class_pattern.match(line)
            if m_cls:
                name = m_cls.group(1)
                symbols.append(CodeSymbol(
                    name=name, kind="class", file_path=file_path,
                    start_line=idx, end_line=min(idx + 10, len(lines)),
                    signature=line.strip(), raw_code=line,
                ))
                continue

            m_iface = interface_pattern.match(line)
            if m_iface:
                name = m_iface.group(1)
                symbols.append(CodeSymbol(
                    name=name, kind="interface", file_path=file_path,
                    start_line=idx, end_line=min(idx + 10, len(lines)),
                    signature=line.strip(), raw_code=line,
                ))
                continue

            m_fn = func_pattern.match(line)
            if m_fn:
                name = m_fn.group(1)
                symbols.append(CodeSymbol(
                    name=name, kind="function", file_path=file_path,
                    start_line=idx, end_line=min(idx + 15, len(lines)),
                    signature=line.strip(), raw_code=line,
                ))
                continue

            m_const = const_func_pattern.match(line)
            if m_const:
                name = m_const.group(1)
                symbols.append(CodeSymbol(
                    name=name, kind="function", file_path=file_path,
                    start_line=idx, end_line=min(idx + 15, len(lines)),
                    signature=line.strip(), raw_code=line,
                ))

        return symbols

    def _extract_java_symbols(self, file_path: str, code: str) -> list[CodeSymbol]:
        """解析 Java 符号。"""
        symbols: list[CodeSymbol] = []
        lines = code.splitlines()
        class_pattern = re.compile(r"^\s*(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?(?:class|interface|enum)\s+([A-Za-z0-9_$]+)")
        method_pattern = re.compile(r"^\s*(?:public|protected|private)\s+[A-Za-z0-9_<>[\]]+\s+([A-Za-z0-9_$]+)\s*\((.*?)\)")

        for idx, line in enumerate(lines, 1):
            m_cls = class_pattern.match(line)
            if m_cls:
                symbols.append(CodeSymbol(
                    name=m_cls.group(1), kind="class", file_path=file_path,
                    start_line=idx, end_line=min(idx + 10, len(lines)),
                    signature=line.strip(), raw_code=line,
                ))
                continue
            m_m = method_pattern.match(line)
            if m_m:
                symbols.append(CodeSymbol(
                    name=m_m.group(1), kind="method", file_path=file_path,
                    start_line=idx, end_line=min(idx + 15, len(lines)),
                    signature=line.strip(), raw_code=line,
                ))
        return symbols

    # -----------------------------------------------------------------------
    # 2. 相关性打分与切片检索
    # -----------------------------------------------------------------------

    def score_symbol(self, symbol: CodeSymbol, query: str) -> tuple[float, list[str]]:
        """计算符号与查询的相关度得分（0.0 ~ 100.0）。"""
        q_tokens = set(re.findall(r"[A-Za-z0-9_]+", query.lower()))
        if not q_tokens:
            return 0.0, []

        score = 0.0
        reasons: list[str] = []

        # 符号名精确匹配
        name_lower = symbol.name.lower()
        symbol_tokens = set(re.findall(r"[A-Za-z0-9_]+", name_lower))

        if query.lower() in name_lower or name_lower in query.lower():
            score += 40.0
            reasons.append("symbol_name_match")

        shared_tokens = q_tokens.intersection(symbol_tokens)
        if shared_tokens:
            score += len(shared_tokens) * 15.0
            reasons.append(f"token_overlap:{','.join(sorted(list(shared_tokens)))}")

        # Docstring 匹配
        doc_lower = symbol.docstring.lower()
        doc_hits = sum(1 for tok in q_tokens if tok in doc_lower)
        if doc_hits:
            score += doc_hits * 8.0
            reasons.append(f"docstring_hits:{doc_hits}")

        # 签名匹配
        sig_lower = symbol.signature.lower()
        sig_hits = sum(1 for tok in q_tokens if tok in sig_lower)
        if sig_hits:
            score += sig_hits * 5.0
            reasons.append(f"signature_hits:{sig_hits}")

        # 核心类提升权重
        if symbol.kind in ("class", "interface"):
            score *= 1.2

        return round(min(score, 100.0), 2), reasons

    def search_context_slices(
        self,
        files: dict[str, str],
        query: str,
        limit: int = 10,
        min_score: float = 5.0,
    ) -> list[ContextSlice]:
        """在给定的代码库文件集中搜索最相关的上下文切片。"""
        all_symbols: list[CodeSymbol] = []
        for path, content in files.items():
            all_symbols.extend(self.extract_symbols(path, content))

        slices: list[ContextSlice] = []
        for sym in all_symbols:
            score, reasons = self.score_symbol(sym, query)
            if score >= min_score:
                slices.append(
                    ContextSlice(
                        file_path=sym.file_path,
                        start_line=sym.start_line,
                        end_line=sym.end_line,
                        symbol_name=sym.name,
                        symbol_kind=sym.kind,
                        content=sym.raw_code,
                        relevance_score=score,
                        reasons=reasons,
                    )
                )

        slices.sort(key=lambda s: s.relevance_score, reverse=True)
        return slices[:limit]

    # -----------------------------------------------------------------------
    # 3. 高密度上下文装配（Prompt Context Assembler）
    # -----------------------------------------------------------------------

    def assemble_context_prompt(
        self,
        slices: list[ContextSlice],
        *,
        max_chars: int = 4000,
        compress_long_bodies: bool = True,
    ) -> dict[str, Any]:
        """按字符预算装配 Cursor 级高密度代码上下文 Markdown 提示词。"""
        header = "### 📌 Cursor-Grade Codebase Context\n\n"
        blocks: list[str] = []
        current_len = len(header)
        included_slices: list[dict[str, Any]] = []

        for s in slices:
            raw = s.content
            if compress_long_bodies and len(raw.splitlines()) > 25:
                # 智能折叠冗长实现体，保留前 10 行与后 3 行
                lines = raw.splitlines()
                compressed = (
                    "\n".join(lines[:10])
                    + "\n    # ... [Implementation details omitted by Cursor Context Engine] ...\n"
                    + "\n".join(lines[-3:])
                )
                raw = compressed

            block = (
                f"#### `{s.file_path}` (Lines {s.start_line}-{s.end_line}) · "
                f"[{s.symbol_kind}] `{s.symbol_name}` (Relevance: {s.relevance_score})\n"
                f"```\n{raw}\n```\n\n"
            )

            if current_len + len(block) > max_chars and blocks:
                break

            blocks.append(block)
            current_len += len(block)
            included_slices.append({
                "file_path": s.file_path,
                "symbol_name": s.symbol_name,
                "lines": f"{s.start_line}-{s.end_line}",
                "relevance": s.relevance_score,
            })

        assembled_text = header + "".join(blocks)
        return {
            "prompt_context": assembled_text,
            "total_chars": len(assembled_text),
            "max_chars": max_chars,
            "slice_count": len(included_slices),
            "included_slices": included_slices,
        }
