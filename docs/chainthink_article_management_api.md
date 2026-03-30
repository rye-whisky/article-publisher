# ChainThink 后台文章/内容管理 API 说明（基于实际后台页面抓取）

更新时间：2026-03-26 13:xx（Asia/Shanghai）
目标页面：
- 后台入口：`https://admin.chainthink.cn/#/layout/chainthink/contentrouterHolder/articleManagement`
- 当前实测已登录页面：`https://admin.chainthink.cn/#/layout/chainthink/contentrouterHolder/newsFlashManagement`

> 说明
>
> 这份文档来自**已登录后台页面的真实请求**、前端资源加载记录、以及已实际调用成功的发布接口。
> 我把内容分成：
> - **已验证**：我已在后台页面或脚本中实际观察/调用到
> - **高概率关联但未完整确认**：从页面资源、页面结构、按钮、前端模块名推断出的接口用途，需要进一步在“文章管理/编辑页”抓包确认
>
> 因为当前浏览器打开落点实际跳到了“快讯管理”页，所以“文章管理新增/编辑/封面上传”的部分，部分字段和接口仍需在文章编辑页再抓一次网络面板做最终定稿。

---

## 1. 域名与服务分层

### 1.1 管理后台前端
- `https://admin.chainthink.cn`

用途：
- 管理端 SPA 前端页面
- 静态资源（JS/CSS）
- 富文本样式等前端资源

### 1.2 内容后台主 API
- `https://api-v2.chainthink.cn`

已观察到的命名空间：
- `/ccs/v1/admin/...`
- `/financial_admin/v1/...`

### 1.3 菜单/用户信息 API
- `https://api.admin.xmodo.cn`

已验证接口：
- `/menu/getMenu`
- `/user/getUserInfo`

用途：
- 后台菜单
- 当前登录用户信息

---

## 2. 认证方式

## 2.1 请求头
已验证管理端接口请求头包含：

```http
x-token: <JWT>
x-user-id: <user_id>
X-App-Id: <app_id>
Content-Type: application/json; charset=utf-8
Origin: https://admin.chainthink.cn
Referer: https://admin.chainthink.cn/
```

## 2.2 已实测可用值
在当前环境中，以下三项组合可成功调用发布接口：

- `x-token`: 后台登录 JWT
- `x-user-id`: `83`
- `X-App-Id`: `101`

## 2.3 认证结论
- **内容管理相关 API 基本都依赖这 3 个头**
- 如果只有 token 没有 `x-user-id` / `X-App-Id`，大概率不够
- 适合通过浏览器开发者工具 Network 面板复用已登录态抓取

---

## 3. 已验证接口清单

## 3.1 发布文章 / 内容

### 接口
`POST https://api-v2.chainthink.cn/ccs/v1/admin/content/publish`

### 状态
**已验证，可成功调用**

### 用途
- 新建一篇内容到后台
- 当前已用于：券商中国、深潮 TechFlow 的文章推送测试

### 最小可用请求头
```http
Accept: application/json, text/plain, */*
Content-Type: application/json; charset=utf-8
Origin: https://admin.chainthink.cn
Referer: https://admin.chainthink.cn/
x-token: <JWT>
x-user-id: 83
X-App-Id: 101
```

### 已实测请求体结构
```json
{
  "id": "0",
  "info": {},
  "is_translate": true,
  "translation": {
    "zh-CN": {
      "title": "文章标题",
      "text": "<p><strong>来源：</strong>深潮 TechFlow</p><p>正文 HTML</p>",
      "abstract": "摘要"
    }
  },
  "type": 5,
  "admin_detail": {},
  "strong_content_tags": {},
  "chain_is_calendar": false,
  "chain_calendar_time": 1774500000,
  "chain_calendar_tendency": 0,
  "is_push_bian": 2,
  "content_pin_top": 0,
  "is_public": false,
  "user_id": "3",
  "chain_fixed_publish_time": 0,
  "as_user_id": "3",
  "is_chain": true,
  "chain_airdrop_time": 0,
  "chain_airdrop_time_end": 0
}
```

### 已确认字段说明
| 字段 | 类型 | 说明 | 状态 |
|---|---|---|---|
| `id` | string | `0` 表示新增 | 已验证 |
| `translation.zh-CN.title` | string | 标题 | 已验证 |
| `translation.zh-CN.text` | string | HTML 正文 | 已验证 |
| `translation.zh-CN.abstract` | string | 摘要 | 已验证 |
| `type` | number | 内容类型，文章测试中使用 `5` | 已验证 |
| `is_translate` | boolean | 启用多语言结构 | 已验证 |
| `is_chain` | boolean | ChainThink 内容 | 已验证 |
| `chain_is_calendar` | boolean | 是否同步到日历 | 已验证 |
| `chain_calendar_time` | number | 日历时间戳 | 已验证 |
| `is_public` | boolean | 是否公开 | 已验证 |
| `content_pin_top` | number | 是否置顶 | 已验证 |
| `is_push_bian` | number | 推送相关控制位，当前成功值为 `2` | 已验证（语义待确认） |
| `user_id` | string | 内容归属用户 | 已验证 |
| `as_user_id` | string | 代发用户 | 已验证 |
| `admin_detail` | object | 管理端扩展字段 | 已验证（当前可为空） |
| `strong_content_tags` | object | 强标签结构 | 已验证（当前可为空） |

### 成功响应
```json
{
  "code": 0,
  "msg": "OK",
  "data": {
    "id": "115899858119659520"
  },
  "trace": "82864541412e7275520b090e5d8afb38"
}
```

### 备注
- 当前脚本发布的“封面图”不是独立接口字段，而是通过正文 HTML 中的 `<img>` 实现展示
- 后台列表中有“封面”列，说明**很可能还存在单独封面字段/上传接口**，但当前尚未从文章编辑页抓包确认

---

## 3.2 获取内容栏目列表

### 接口
`GET/POST https://api-v2.chainthink.cn/financial_admin/v1/content/get_user_contents_column`

### 状态
**已验证存在，页面实际请求过**

### 推定用途
- 获取用户可用栏目/频道/内容分类
- 页面筛选项或编辑页栏目选择器的数据源

### 证据
该接口在内容管理页面资源加载时被请求。

### 待确认点
- 请求方法
- 参数结构
- 返回字段（栏目 ID、栏目名、父子层级）

---

## 3.3 获取内容来源列表

### 接口
`GET/POST https://api-v2.chainthink.cn/financial_admin/v1/get_articl_source_list`

### 状态
**已验证存在，页面实际请求过**

### 推定用途
- “内容来源”下拉框数据源
- 文章/快讯列表筛选中的来源枚举

### 已知拼写
- 接口路径里是 `articl`，不是 `article`

### 待确认点
- 请求方法
- 返回字段（来源 code / label）

---

## 3.4 内容列表查询（Chain 内容列表）

### 接口
`GET/POST https://api-v2.chainthink.cn/ccs/v1/admin/content/chain_list`

### 状态
**已验证存在，页面实际请求过**

### 推定用途
- 列表页主表格数据来源
- 文章/快讯内容列表查询

### 页面可见筛选项（已验证）
从后台内容管理页的表单结构可见，列表查询大概率支持以下筛选参数：

| 页面字段 | 说明 |
|---|---|
| 内容状态 | 如：全部 / 待发布 / 已发布 等 |
| 内容来源 | 来源枚举 |
| 排序方式 | 如：发布时间降序 |
| 创建时间 | 起止范围 |
| 发布时间 | 起止范围 |
| 日历日期 | 起止范围 |
| 内容标题 | 标题模糊搜索 |

### 页面可见列表列（已验证）
主表格列包括：
- ID
- 标题
- 封面
- 栏目
- 标签
- 来源
- 是否精选
- 发布状态
- 同步至日历
- 创建时间
- 发布时间
- 最近更新时间
- 最近操作人
- 操作

### 页面可见行级动作（已验证）
每条内容可见按钮：
- 编辑
- 全员推送
- 移入回收站
- 查看

### 待确认点
- 真实请求方法
- 分页参数命名（可能是 `page` / `pageSize` 或 `pageIndex` / `page_size`）
- 列表返回 JSON 结构
- 行级按钮对应接口

---

## 3.5 菜单接口

### 接口
`GET/POST https://api.admin.xmodo.cn/menu/getMenu`

### 状态
**已验证存在，页面实际请求过**

### 用途
- 获取后台左侧菜单/顶部菜单

---

## 3.6 当前用户信息接口

### 接口
`GET/POST https://api.admin.xmodo.cn/user/getUserInfo`

### 状态
**已验证存在，页面实际请求过**

### 用途
- 获取登录用户信息
- 页面右上角用户 `whisky` 数据来源之一

---

## 4. 页面结构与动作说明（基于已登录后台 UI）

## 4.1 内容管理模块结构
已看到菜单路径：
- 运营管理
- 内容管理
  - 知识库管理
  - 空投管理
  - 文章管理
  - 快讯管理

## 4.2 快讯管理页可见动作
- 查询
- 重置
- 发快讯
- 批量标签
- 编辑
- 全员推送
- 移入回收站
- 查看

## 4.3 文章管理页高概率动作
根据菜单结构与已成功发布内容推断，文章管理页高概率至少包含：
- 发文章 / 新建文章
- 编辑文章
- 查看文章
- 删除 / 移入回收站
- 设置栏目
- 设置标签
- 上传封面
- 发布 / 定时发布
- 置顶 / 精选 / 同步日历

> 这些动作需要进一步在文章管理页点击一次“新增/编辑”后抓包确认具体接口。

---

## 5. 封面相关分析

## 5.1 已知事实
- 列表页表格中存在“封面”列
- 当前 `content/publish` 接口在仅传正文 HTML 图片时也能成功创建内容
- 但这并不等价于“真正设置了后台封面字段”

## 5.2 高概率情况
后台封面能力大概率有两种实现方式之一：

### 方案 A：发布接口里有独立封面字段
可能出现在这些位置：
- `info.cover`
- `info.image`
- `admin_detail.cover`
- `translation.zh-CN.cover`

### 方案 B：先上传文件，再把返回 URL/ID 写入发布接口
从前端资源名可见这些模块：
- `fileUploadAndDownload.C86AgqgO.js`
- `upload-manager.Dnwd0spO.js`
- `Upload.DEuHW9gp.js`
- `preview.CLxA71zR.js`

这说明后台**肯定有上传体系**，并且文章封面大概率走上传接口，不太可能只靠正文 `<img>`。

## 5.3 当前结论
- **“封面上传”能力大概率存在**
- **但具体接口路径 / 参数 / 返回值目前未最终确认**
- 若要把封面补成真正 API 文档，需要在文章编辑页点击“上传封面”抓一次请求

---

## 6. 前端资源线索（用于继续逆向）

已观察到的关键前端资源模块：
- `page.-HGZNiXz.js`
- `edit.Bv8LFkUC.js`
- `fileUploadAndDownload.C86AgqgO.js`
- `upload-manager.Dnwd0spO.js`
- `Upload.DEuHW9gp.js`
- `preview.CLxA71zR.js`
- `BatchTagDialog.DAi0LjtZ.js`
- `useKnowledgeBaseColumnList.snecTBS4.js`

这些文件名对应的含义：
- `page.*.js`：列表页逻辑
- `edit.*.js`：新增/编辑页逻辑
- `fileUploadAndDownload.*.js` / `upload-manager.*.js` / `Upload.*.js`：上传下载体系
- `preview.*.js`：内容预览
- `BatchTagDialog.*.js`：批量标签功能

---

## 7. 当前可直接复用的最小“发内容”调用方案

如果只是**自动发布文章到后台**，当前已验证可直接调用：

### Endpoint
```http
POST https://api-v2.chainthink.cn/ccs/v1/admin/content/publish
```

### Body 要点
- `id = "0"`
- `translation.zh-CN.title`
- `translation.zh-CN.text`
- `translation.zh-CN.abstract`
- `type = 5`
- `is_chain = true`

### 优点
- 已跑通
- 可批量自动化
- 能插入正文图片

### 局限
- 目前不确定是否真正设置了后台“封面字段”
- 不确定栏目/标签/精选/发布状态控制的完整字段集合

---

## 8. 建议的下一步抓包任务

如果要把“文章管理全 API”补到完整可开发文档，建议按下面顺序再抓一次：

### 任务 1：打开文章管理页主列表
目标：确认
- 列表查询接口参数
- 列表返回结构
- 各筛选字段对应参数名

### 任务 2：点击“发文章”或“新增文章”
目标：确认
- 初始化接口
- 栏目列表 / 来源列表 / 标签列表接口
- 编辑页保存接口是否仍然是 `content/publish`

### 任务 3：点击“上传封面”
目标：确认
- 上传接口 URL
- multipart/form-data 还是分片上传
- 返回结构（URL / file_id / media_id）
- 最终在发布请求中的字段落点

### 任务 4：点击“编辑”一篇已有文章
目标：确认
- 获取详情接口
- 更新接口（可能仍然 `content/publish`，只是 `id != 0`）

### 任务 5：测试“移入回收站 / 全员推送 / 查看”
目标：确认
- 删除/回收站接口
- 推送接口
- 详情接口或预览接口

---

## 9. 当前结论总结

### 已经明确可用的接口
1. `POST /ccs/v1/admin/content/publish` —— 发内容（已实测成功）
2. `/financial_admin/v1/content/get_user_contents_column` —— 栏目列表（页面已请求）
3. `/financial_admin/v1/get_articl_source_list` —— 来源列表（页面已请求）
4. `/ccs/v1/admin/content/chain_list` —— 内容列表（页面已请求）
5. `/menu/getMenu` —— 菜单
6. `/user/getUserInfo` —— 用户信息

### 已经明确存在但未最终定稿的能力
- 文章编辑页接口
- 封面上传接口
- 标签批量操作接口
- 全员推送接口
- 回收站接口
- 文章详情接口

---

## 10. 如果你要我继续

我可以继续做下一轮更完整的文档，把以下内容补齐：
- 文章管理页真实列表 API 参数/返回值
- 新增文章完整请求体
- 封面上传接口
- 编辑文章接口
- 删除/回收站/推送接口

要做到这一步，最佳方式是：
1. 保持后台已登录
2. 我直接在后台页面上逐个点“文章管理 / 新增 / 上传封面 / 编辑”
3. 把真实接口全部补成最终版文档
