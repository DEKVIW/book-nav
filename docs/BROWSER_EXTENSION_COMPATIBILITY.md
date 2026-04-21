# 浏览器扩展兼容性说明

## 更新说明：推荐保留浏览器原生 `title`

针对 `Bitwarden + 卡片 Tooltip` 导致的 Chrome / Chromium 标签切换卡顿问题，当前更推荐采用下面的方案：

1. 禁用卡片自定义 Tooltip
2. 保留卡片 hover 样式、拖拽排序、右键菜单等其他交互
3. 如仍需要提示文本，优先保留浏览器原生 `title`，不要继续使用自定义 Tooltip

原因是浏览器原生 `title` 不需要额外创建 Tooltip 浮层节点，也不需要 hover 时做额外的 JS 定位和重排，和 Bitwarden 的冲突概率更低。

## 现象

在 Chrome / Chromium 内核浏览器中，如果 `Bitwarden` 扩展处于开启状态，同时导航站首页、分类页或搜索结果页中的网站卡片启用了 Tooltip，可能出现以下问题：

- 鼠标连续悬停多张网站卡片后，切换浏览器标签页明显卡顿
- 卡顿会随着 hover 次数增加而变得更明显
- 同一环境下，关闭 Bitwarden 后现象通常会明显减轻或消失

## 排查结论

本项目已针对该问题做过多轮排查，结论如下：

- 冲突点不局限于 `Bootstrap Tooltip`
- 即使改为项目内自定义 Tooltip，只要卡片 hover 期间仍有 Tooltip 浮层相关的 DOM / 事件链路，问题仍可能复现
- 拖拽排序本身不是主要根因，真正高相关的是“卡片 hover + Tooltip + Bitwarden 扫描”
- 最稳妥的规避方式是：不要让网站卡片继续使用自定义 Tooltip 链路

## 影响范围

重点影响以下场景：

- 首页网站卡片
- 分类页网站卡片
- 搜索结果中动态渲染的网站卡片

## 本地临时验证方法

可以先在浏览器控制台执行以下代码，把卡片的自定义 Tooltip 降级为原生 `title`：

```js
document.querySelectorAll('.site-card').forEach((el) => {
  const customText = el.getAttribute('data-tooltip');
  const nativeText = el.getAttribute('title');

  if (customText && !nativeText) {
    el.setAttribute('title', customText);
  }

  el.removeAttribute('data-tooltip');
});

document.querySelectorAll('.site-card-tooltip, .tooltip').forEach((el) => {
  el.remove();
});
```

然后执行以下命令检查页面中是否仍残留自定义 Tooltip：

```js
document.querySelectorAll(
  '.site-card[data-tooltip], .site-card-tooltip, .tooltip'
).length
```

如果返回值为 `0`，再重复执行“多次悬停卡片 -> 切换浏览器标签页”的操作，观察卡顿是否明显减轻。

## 用户脚本方案

如果希望在本地浏览器中长期保留“原生 `title` Tooltip”并禁用卡片自定义 Tooltip，可以使用下面这份最小用户脚本。

适用站点：

- `https://nav.yilancn.top/`

### 推荐用户脚本

```javascript
// ==UserScript==
// @name         nav keep native title tooltip only
// @namespace    nav-fix
// @version      1.0
// @match        *://nav.yilancn.top/*
// @run-at       document-end
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  function normalizeCardTooltips(root = document) {
    const cards = [];

    if (root.nodeType === 1 && root.matches(".site-card")) {
      cards.push(root);
    }

    if (root.querySelectorAll) {
      cards.push(...root.querySelectorAll(".site-card"));
    }

    cards.forEach((el) => {
      const customText = el.getAttribute("data-tooltip");
      const nativeText = el.getAttribute("title");

      if (customText && !nativeText) {
        el.setAttribute("title", customText);
      }

      el.removeAttribute("data-tooltip");
    });

    document.querySelectorAll(".site-card-tooltip, .tooltip").forEach((el) => el.remove());
  }

  function showBadge() {
    if (document.getElementById("nav-tooltip-native")) return;

    const el = document.createElement("div");
    el.id = "nav-tooltip-native";
    el.textContent = "已切换为浏览器原生 Tooltip";
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
    normalizeCardTooltips(root);
    document.documentElement.setAttribute("data-nav-tooltip-mode", "native-title");
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

## 用户脚本安装步骤

1. 安装 `Tampermonkey` 或 `Violentmonkey`
2. 新建脚本
3. 将上面的脚本完整粘贴进去并保存
4. 刷新 `https://nav.yilancn.top/`
5. 观察页面右下角是否出现 `已切换为浏览器原生 Tooltip`

## 用户脚本验证方法

保存脚本并刷新页面后，可以在控制台执行以下命令确认脚本已经生效：

```js
document.documentElement.getAttribute('data-nav-tooltip-mode')
```

预期返回：

```js
"native-title"
```

再执行：

```js
document.querySelectorAll(
  '.site-card[data-tooltip], .site-card-tooltip, .tooltip'
).length
```

预期返回：

```js
0
```

如果还想确认卡片是否保留了原生 `title`，可以再执行：

```js
document.querySelectorAll('.site-card[title]').length
```

正常情况下，这个值通常会大于 `0`。

## 已确认会踩的坑

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
- 页面右下角也不会出现提示标记

## 备注

这个问题更偏向“浏览器扩展兼容性问题”，不代表项目的拖拽、搜索或普通卡片渲染逻辑本身存在同等级别的性能缺陷。实际处理时，应优先从 Tooltip 兼容性角度规避。
