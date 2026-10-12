# GitHub Pages 的 Gzip 与协商缓存方案

本项目继续发布根目录的 `index.html`，保留 `.nojekyll`，使用 GitHub Pages 原生的 HTTP 压缩和缓存。网站仍是单文件应用，答题记录仍由浏览器本地保存。

## 平台配置与实际响应

GitHub Pages 的服务器和 CDN 由平台管理，仓库不能像自建 Nginx 一样设置响应头。上传 `.gz` 文件、添加 `.htaccess`、`_headers` 或 HTML 缓存 meta 标签，都不能替代平台的响应头配置。本方案采用平台已有的能力，并增加部署后的持续验收。

2026-10-12 对 `https://linkmo0225.github.io/tju-party-quiz/` 的 GET 实测：

| 项目 | 实测结果 |
| --- | --- |
| 普通响应 | HTTP 200，284,344 字节 |
| Gzip 响应 | HTTP 200，88,543 字节，减少 68.86% |
| 内容一致性 | Gzip 解压后与普通响应逐字节一致 |
| 压缩协商 | `Content-Encoding: gzip`、`Vary: Accept-Encoding` |
| 缓存策略 | `Cache-Control: max-age=600` |
| 版本验证标识 | 普通和压缩响应各自提供 `ETag`，并提供 `Last-Modified` |
| ETag 校验 | 普通和压缩响应的 `If-None-Match` 均返回 304，响应正文为 0 字节 |
| 修改时间校验 | `If-Modified-Since` 返回 304，响应正文为 0 字节 |
| 过期标识 | 不匹配的 ETag 返回 200，即使同时带有当前修改时间，也能取回完整的当前页面 |

这些压缩和缓存能力在本次 4.3 修改前就已经生效。本次新增的是验证脚本、发布验收工作流和配置说明，不把平台已有能力计作新增性能收益。页面大小会随题库和更新日志变化；600 秒缓存时间也是平台当前行为，不是仓库可以自定义的承诺。

Chrome 和 Edge 的真实浏览器测试也确认：首次导航使用 Gzip，刷新时自动携带 ETag，并收到网络层的 304。Navigation Timing 报告的传输量由 88,843 字节降为 300 字节；300 字节包含浏览器估算的响应头开销，304 的响应正文实际为 0 字节。

## 本地与线上验收

只需要 Python 3 标准库，不安装第三方依赖。脚本最多发送 6 次只读 GET 请求，分别检查两种传输形式、两种 ETag、修改时间和过期 ETag。失败会返回非零退出码，并保留失败检查。

```powershell
python scripts/test_pages_http.py
python scripts/verify_pages_http.py https://linkmo0225.github.io/tju-party-quiz/ --output pages-http-report.json
```

发布新版本后再加入 `--expected-file`，检查线上内容是否与本次发布一致；仅允许 Git 换行转换产生的 CRLF/LF 差异：

```powershell
python scripts/verify_pages_http.py https://linkmo0225.github.io/tju-party-quiz/ --expected-file index.html --output pages-http-report.json
```

该命令应在 Pages 已发布对应版本后执行。版本号不同，或者版本号相同但页面内容不同，都会导致内容一致性检查失败，不能把“Gzip 和 304 可用”当作“本次修改已上线”。报告包含实际响应头、响应大小、页面 SHA-256、检测到的页面版本和每项验收结果。

## 部署后的 Actions 验收

新增 `.github/workflows/pages-http-check.yml`，在当前仓库的 `pages-build-deployment` 成功完成后自动检查对应提交，也支持手动运行。该事件名称已通过原仓库的公开工作流列表确认。

工作流使用只读权限读取本仓库实际 Pages 地址，支持 fork 和自定义域名；手动输入网址可以覆盖默认地址。它比较部署对应提交中的 `index.html`，最多尝试 3 次，每次失败后间隔 30 秒，以容纳短暂的发布传播延迟；所有尝试的结果均保留在 `pages-http-report` artifact 中。工作流文件进入默认分支后，自动触发才会生效。

如果将来改为自定义 Pages 部署工作流，应把 `workflow_run.workflows` 改成那个工作流的实际名称。工作流不会部署页面或修改 Pages 设置。

## 更新与缓存的取舍

在平台的 10 分钟有效期内，浏览器可以直接复用页面；过期或重新验证时，通过 ETag/修改时间使用 304，减少重复传输。浏览器和 CDN 的缓存传播可能让发布后的一段时间内仍看到旧页面。

发布后先等待 Pages 部署成功，再进行内容一致性检查。若仍检测到旧内容，应检查部署来源分支、提交和缓存传播，并稍后重试验收。用户立即查看更新时可以使用浏览器强制刷新，但不能据此承诺所有访问者立即获得新版本。

若以后必须自定义缓存时长、保证统一的立即失效，才考虑增加可配置的代理/CDN。本次继续使用现有 GitHub Pages 地址，不改变浏览器进度所属的网站来源。

平台参考：[GitHub Pages 文档](https://docs.github.com/en/pages)、[workflow_run 事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)。
