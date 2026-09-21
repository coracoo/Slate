# 开源前的隐私与发布检查

## 固定发布仓库

开源发布目标为 `https://github.com/coracoo/Slate`，默认分支 `main`。发布前执行候选文件与历史扫描、测试和前端构建；只推送明确分支，不使用镜像推送，避免带入备份或私人引用。

## 发布内容

保留引擎、工作台源码、前端源码与锁文件、测试、空凭证示例、可复用 Skill。Python 第三方依赖由 `requirements.txt` 安装，前端依赖由 npm 安装，不提交本机安装副本。

以下数据仅留本机，并由 `.gitignore` 排除：

- `projects/` 中的剧本、人物、音色、视频、生成结果及测试素材。
- 服务商密钥配置、公网出口签名密钥、旧 MCP 配置及备份、`.env` 系列文件和私钥文件。
- 日志、任务记录、缓存、下载包、运行时、依赖目录及 Git 备份目录。
- `workbench/source_manifest.json` 的个人素材来源路径。
- `previs_system/knowledge/skills.json` 和 `user_cards.json` 的个人知识与项目片例。程序在缺失时使用空知识库，之后可自行构建或添加。
- 内部审查记录及未审阅文档。可公开文档按 `.gitignore` 白名单逐份加入，不批量开放私人工作笔记。

`.gitignore` 不能擦除提交历史，也不会自动取消已跟踪文件。本次取消私人文件和依赖目录的索引跟踪，仅删除 Git 索引项，本地文件仍在。

## 检查命令

```bash
python workbench/tools/audit_public_repo.py
python workbench/tools/audit_public_repo.py --history
git ls-files -ci --exclude-standard
git diff --cached --stat
```

扫描器只报告位置，不输出密钥正文。它覆盖常见密钥格式和私人文件路径，不替代人工审查。发布前还应检查图片、样例、文档及第三方许可。

## 历史与服务边界

旧历史可能仍包含已移出的本地数据。未经历史清理，不应把整个旧 `.git` 直接推送为公开仓库。可另建仅含审查后工作树的干净仓库，或备份后明确执行历史改写。

工作台管理服务主要面向可信本机/局域网，不应把全部管理 API 直接暴露到互联网。公网素材出口只转发带签名的 `/api/public-reference` 路径；真实地址与密钥保留本机。
