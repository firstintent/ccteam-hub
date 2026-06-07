# ccteam-hub — ccteam 插件市场仓库

ccteam **引擎之外**的「提示词类型」插件集中地。ccteam 引擎仓本身 **零提示词内容**(engine only);所有 role/agent、skill、workflow 的**内容**住这里(自建 + 开源接入),由 ccteam 的**插件市场**(web UI + CLI)浏览 + 安装到用户项目。

> 状态:**scaffold**(骨架 + 文档,内容待填充)。

## 布局

```
agents/        role / subagent persona —— Claude-native `.md`(YAML frontmatter + body)
skills/        skill —— 每个一个目录:<name>/SKILL.md (+ body 文件)
workflows/     workflow —— 每个一个目录:<name>/workflow.yaml (+ 可选 agents/)
index.json     市场目录索引(列举全部插件,供 ccteam 市场 UI/CLI 拉取)
```

## index.json schema(草案 v1)

```jsonc
{
  "version": 1,
  "name": "ccteam-hub",
  "plugins": [
    {
      "id": "reviewer",                // 唯一 id(= 安装后的 stem,[a-z0-9_-])
      "type": "agent",                 // "agent" | "skill" | "workflow"
      "name": "Reviewer",
      "description": "对抗式代码复审 …",
      "path": "agents/reviewer.md",    // 仓内相对路径
      "source": "builtin",             // "builtin" | "agency-agents" | "<其它开源源>"
      "license": "MIT",
      "tags": ["review", "qa"]
    }
  ]
}
```

## 来源(source)

- **builtin**:本仓自建插件。
- **开源接入**:如 agency-agents 等(Claude-native `.md`,MIT)—— 通过 ingestion 同步进来,**保留来源 + license**;`.md` 内容 verbatim,仅 sanitize stem 到 `[a-z0-9_-]`。

## ccteam 怎么消费

ccteam 的市场 UI / CLI(`ccteam role|skill|workflow add …`)按 `index.json` 列举本仓(+ 配置的开源源),把选中的插件装到用户项目:
- agent/role → 项目 `.claude/agents/<id>.md`
- skill → 项目 skill 目录
- workflow → 项目 workflow 目录

## 注意

- 与 ccteam **引擎仓分离**(独立 git;ccteam 仓的 `.gitignore` 已排除本目录)。
- **`cto` persona 是唯一例外**,留在 ccteam 引擎仓(bootstrap 默认管家、算引擎配置),**不**在本 hub。
- 红线:ccteam 引擎仓不含任何提示词类型插件;新增/迁入的 role/skill/workflow 一律进本 hub。
