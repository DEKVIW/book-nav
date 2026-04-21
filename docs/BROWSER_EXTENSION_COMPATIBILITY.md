# 浏览器扩展兼容性说明

## Bitwarden 与卡片 Tooltip 冲突

### 现象

在 Chrome / Chromium 内核浏览器中，如果 `Bitwarden` 扩展处于开启状态，同时导航站首页或分类页的网站卡片启用了悬停 Tooltip，可能出现以下问题：

- 鼠标连续悬停多张网站卡片后，切换浏览器标签页明显卡顿
- 卡顿会随着 hover 次数增加而变得更明显
- 同一环境下，关闭 Bitwarden 后现象通常会明显减轻或消失

### 排查结论

本项目已针对该问题做过多轮排查，结论如下：

- 冲突点不局限于 `Bootstrap Tooltip`
- 即使改为项目内自定义 Tooltip，只要卡片 hover 期间仍有 Tooltip 浮层相关的 DOM / 事件链路，问题仍可能复现
- 临时移除网站卡片上的 Tooltip 属性后，卡顿现象可明显缓解
- 拖拽排序本身不是主要根因，真正高相关的是“卡片 hover + Tooltip + Bitwarden 扫描”

### 影响范围

重点影响以下场景：

- 首页网站卡片
- 分类页网站卡片
- 搜索结果中动态渲染的网站卡片

### 建议处理

推荐按下面顺序处理：

1. 优先禁用网站卡片 Tooltip
2. 保留卡片 hover 样式、拖拽排序、右键菜单等其他交互
3. 如果必须做本地兼容处理，优先使用用户脚本在浏览器侧移除卡片 Tooltip，而不是继续叠加新的 Tooltip 实现

### 本地验证方法

可以先在浏览器控制台执行以下代码，临时禁用当前页面的卡片 Tooltip：

```js
document.querySelectorAll('.site-card').forEach((el) => {
  el.removeAttribute('data-tooltip');
  el.removeAttribute('title');
});

document.querySelectorAll('.site-card-tooltip, .tooltip').forEach((el) => {
  el.remove();
});
```

然后用以下命令检查是否仍有卡片 Tooltip 残留：

```js
document.querySelectorAll(
  '.site-card[data-tooltip], .site-card[title], .site-card-tooltip, .tooltip'
).length
```

如果返回值为 `0`，再重复执行“多次悬停卡片 -> 切换浏览器标签页”的操作，观察卡顿是否明显减轻。

### 用户脚本注意事项

如果使用 `Tampermonkey` / `Violentmonkey` 一类用户脚本扩展做本地兼容，请确认扩展本身已经具备注入权限，否则脚本可能显示“已启用”但实际上未生效。

需要重点检查：

- 已开启浏览器扩展的开发者模式
- 已开启脚本扩展的“允许运行用户脚本”
- 站点访问权限已覆盖目标站点

### 推荐用户脚本

如果希望在本地浏览器中长期禁用导航站卡片 Tooltip，可以使用下面这份最小用户脚本。

适用站点：

- `https://nav.yilancn.top/`

脚本内容：

```javascript
// ==UserScript==
// @name         nav disable card tooltips
// @namespace    nav-fix
// @version      1.0
// @match        *://nav.yilancn.top/*
// @run-at       document-end
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  function disableCardTooltips(root = document) {
    const nodes = [];

    if (root.nodeType === 1 && root.matches(".site-card, .site-card .drag-handle, .site-card .private-badge")) {
      nodes.push(root);
    }

    if (root.querySelectorAll) {
      nodes.push(
        ...root.querySelectorAll(".site-card, .site-card .drag-handle, .site-card .private-badge")
      );
    }

    nodes.forEach((el) => {
      el.removeAttribute("data-tooltip");
      el.removeAttribute("title");
    });

    document.querySelectorAll(".site-card-tooltip, .tooltip").forEach((el) => el.remove());
  }

  function showBadge() {
    if (document.getElementById("nav-tooltip-off")) return;

    const el = document.createElement("div");
    el.id = "nav-tooltip-off";
    el.textContent = "Tooltip 已关闭";
    el.style.cssText = [
      "position:fixed",
      "right:16px",
      "bottom:16px",
      "z-index:2147483647",
      "padding:8px 12px",
      "border-radius:10px",
      "background:#111",
      "color:#fff",
      "font-size:12px",
      "line-height:1.4",
      "pointer-events:none",
      "opacity:.9"
    ].join(";");
    document.body.appendChild(el);
  }

  function applyAll(root = document) {
    disableCardTooltips(root);
    document.documentElement.setAttribute("data-nav-tooltip-disabled", "true");
    showBadge();
  }

  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      if (mutation.type === "attributes") {
        applyAll(mutation.target);
      }

      mutation.addedNodes.forEach((node) => {
        if (node.nodeType === 1) {
          applyAll(node);
        }
      });
    });
  });

  function boot() {
    applyAll(document);

    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["title", "data-tooltip"]
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
```

### 用户脚本安装步骤

1. 安装 `Tampermonkey` 或 `Violentmonkey`
2. 新建脚本
3. 将上面的脚本完整粘贴进去并保存
4. 刷新 `https://nav.yilancn.top/`
5. 观察页面右下角是否出现 `Tooltip 已关闭`

### 用户脚本验证方法

保存脚本并刷新页面后，可以在控制台执行以下命令确认脚本已经生效：

```js
document.documentElement.getAttribute('data-nav-tooltip-disabled')
```

预期返回：

```js
"true"
```

再执行：

```js
document.querySelectorAll(
  '.site-card[data-tooltip], .site-card[title], .site-card .drag-handle[title], .site-card .private-badge[title], .site-card-tooltip, .tooltip'
).length
```

预期返回：

```js
0
```

### 已确认会踩的坑

这次排查中，已经确认以下问题会导致“脚本看起来已启用，但实际上没有注入页面”：

- 只开启了浏览器的开发者模式，但没有在脚本扩展里开启“允许运行用户脚本”
- 没有开启脚本扩展的“允许访问文件网址”
- 站点访问权限没有覆盖目标域名
- 扩展已开启，但脚本本身未保存成功或未匹配到正确域名

推荐检查顺序：

1. 浏览器扩展页已开启开发者模式
2. 脚本扩展详情页已开启“允许运行用户脚本”
3. 脚本扩展详情页已开启“允许访问文件网址”
4. 站点访问权限已允许 `nav.yilancn.top`
5. 用户脚本 `@match` 已覆盖 `*://nav.yilancn.top/*`

如果这些步骤未全部满足，最常见的现象就是：

- 扩展面板里脚本显示“已启用”
- 但页面控制台验证失败
- 页面右下角也不会出现“Tooltip 已关闭”

### 备注

这个问题更偏向“浏览器扩展兼容性问题”，不代表项目的拖拽、搜索或普通卡片渲染逻辑本身存在同等级别的性能缺陷。实际处理时，应优先从 Tooltip 兼容性角度规避。
