# 技能包规范 · 根目录集与标签/版本/交付惯例（P11 · A-内置模块-02/08）

## 1. 规范技能根目录集（A-内置模块-02，根目录落定）

```
skills_packages/            ← 技能包根（每包一个子目录，包名 kebab-case）
  <skill-name>/
    SKILL.md                ← 入口（frontmatter: name/version/tags/description）
    assets/                 ← 技能自带静态资源（可选）
scripts/                    ← 一次性运维脚本（不入技能包）
prompts_packages/           ← 提示词内容包（roles/=_defaults/=scaffold/ 各归其主）
```

## 2. 标签 / 版本 / 交付惯例（A-内置模块-08）

- **版本**：SKILL.md frontmatter `version: MAJOR.MINOR`；行为不兼容改 MAJOR，内容修订改 MINOR
- **标签**：frontmatter `tags:` 列表，首标签=类目（product / dev / ops / content）
- **交付惯例**：新包交付 = 包目录 + SKILL.md 自描述 + 至少 1 条冒烟验证记录（命令+输出）；不满足三件套不算交付
- **禁令**：技能包内不得放秘密；不得依赖仓库外绝对路径（可移植性）
