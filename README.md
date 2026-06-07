# ccteam-hub — ccteam 插件市场仓库

ccteam **引擎之外**的「提示词类型」插件集中地。ccteam 引擎仓本身 **零提示词内容**(engine only);所有 role/agent、skill、workflow 的**内容**住这里(自建 + 开源接入),由 ccteam 的**插件市场**(web UI + CLI)浏览 + 安装到用户项目。

> 状态:**已接入 agency-agents**(192 个 Claude-native agent persona,MIT,verbatim 接入)。

## 布局

```
agents/        role / subagent persona —— Claude-native `.md`(YAML frontmatter + body)
skills/        skill —— 每个一个目录:<name>/SKILL.md (+ body 文件)
workflows/     workflow —— 每个一个目录:<name>/workflow.yaml (+ 可选 agents/)
index.json     市场目录索引(列举全部插件,供 ccteam 市场 UI/CLI 拉取)
sources.json   开源来源声明(repo + pinned sha + license + glob map)
scripts/sync.py  幂等 ingestion 管线(克隆来源 @sha → verbatim 拷入 → 重建 index.json)
LICENSES/      各开源来源的 LICENSE 原文(+ repo + sha 头)
```

## index.json schema(v1)

```jsonc
{
  "version": 1,
  "name": "ccteam-hub",
  "description": "…",
  // 由 ingestion 写入,派生自来源 commit date(非 wall-clock)→ 同 sha 重跑字节一致
  "generated_at": "2026-06-05T17:23:01+09:00",
  "plugins": [
    {
      "id": "backend-development-graphql-architect", // 唯一 id([a-z0-9_-];撞名时加 <division>- 前缀)
      "type": "agent",                 // "agent" | "skill" | "workflow"
      "name": "backend-development-graphql-architect",
      "description": "…",             // 取 frontmatter description(截断到 ~400 字)
      "path": "agents/backend-development-graphql-architect.md", // 仓内相对路径
      "content_sha": "…",             // 拷入文件字节的 sha256(完整性 + diff 检测)
      "source": "agency-agents",       // "builtin" | "agency-agents" | "<其它开源源>"
      "upstream": "https://github.com/wshobson/agents/blob/<sha>/plugins/…/x.md", // 原始出处
      "license": "MIT",
      "tags": ["backend-development"]  // [division]
    }
  ]
}
```

## 来源(source)与 ingestion

- **builtin**:本仓自建插件(直接写入 `agents/` 等,不经 ingestion)。
- **开源接入**:由 `sources.json` 声明,经 `scripts/sync.py` 同步进来,**保留来源 + license + upstream URL**;`.md` 内容 **verbatim**,仅 sanitize 文件名(stem)到 `[a-z0-9_-]`。

当前 `sources.json`:

| name | repo | license | ref(pinned sha) | map |
|---|---|---|---|---|
| `agency-agents` | [wshobson/agents](https://github.com/wshobson/agents) | MIT | `cf6059d030bf4fe96623ae2e596d2f31e35fedc0` | `plugins/*/agents/*.md` → `agent` |

### 跑 sync

```sh
python3 scripts/sync.py     # 仅 stdlib(subprocess/json/hashlib/pathlib/re/glob)
```

对每个来源:full clone → `git checkout <ref>` → 按 `map` glob → 每个文件 verbatim 拷入对应目录(`agent→agents/`、`skill→skills/`、`workflow→workflows/`)→ 重建 `index.json`。

- **id 方案**:sanitize `.md` stem 到 `[a-z0-9_-]`(小写、非字母数字→`-`、合并连续 `-`)。**跨 division 撞名**时,该 stem 的**所有**实例都加 `<division>-` 前缀(确定性,而非任意挑一个保留裸 stem);单独出现的 stem 保留裸名。本接入 192 文件 → 192 个全局唯一 id(其中 95 个加了 division 前缀,对应 30 个跨 division 撞名的 stem;0 个二次撞名)。该方案与上游自己的 `name:` 约定完全一致。
- **幂等(硬性)**:不写 wall-clock 时间戳 —— `generated_at` 派生自来源 commit date(`git show -s --format=%cI`),plugins 按 `id` 排序;**同 sha 重跑字节一致**。重跑前会先清理该来源上一轮拷入的文件(removed-upstream 不残留)。
- **`.github/workflows/sync.yml`**:`workflow_dispatch` + 每周 schedule 跑 `scripts/sync.py` 并自动 commit 变更(pinned sha 下仅当 `sources.json` 升 ref 时才产生 diff)。

## ccteam 怎么消费

ccteam 引擎读本仓 `index.json` 列举全部插件,拉取 `path` 指向的内容,装到用户项目(`ccteam role|skill|workflow add …`):
- agent/role → 项目 `.claude/agents/<id>.md`
- skill → 项目 skill 目录
- workflow → 项目 workflow 目录

`content_sha` 供完整性校验 / 升级 diff;`upstream` + `LICENSES/` 保留开源出处与 license。开源内容为 **verbatim 拷贝**(MIT,署名保留)。

## 注意

- 与 ccteam **引擎仓分离**(独立 git;ccteam 仓的 `.gitignore` 已排除本目录)。
- **`cto` persona 是唯一例外**,留在 ccteam 引擎仓(bootstrap 默认管家、算引擎配置),**不**在本 hub。
- 红线:ccteam 引擎仓不含任何提示词类型插件;新增/迁入的 role/skill/workflow 一律进本 hub。
