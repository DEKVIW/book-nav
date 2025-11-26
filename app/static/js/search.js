document.addEventListener("DOMContentLoaded", function () {
  const searchForm = document.getElementById("searchForm");
  const searchInput = document.getElementById("searchInput");
  const clearSearchBtn = document.getElementById("clearSearch");
  const searchResults = document.getElementById("searchResults");
  const searchKeyword = document.getElementById("searchKeyword");
  const resultsContent = document.getElementById("resultsContent");
  const noResults = document.getElementById("noResults");
  const categoriesContainer = document.getElementById("categoriesContainer");
  const aiSearchToggle = document.getElementById("aiSearchToggle");

  // 搜索功能
  searchForm.addEventListener("submit", function (e) {
    e.preventDefault();
    const query = searchInput.value.trim();
    console.log("搜索提交:", query);

    if (query) {
      // 检查是否启用AI搜索
      const useAI = aiSearchToggle && aiSearchToggle.checked;
      console.log("AI搜索状态:", useAI);

      // 显示加载状态
      const loadingText = useAI ? "正在使用AI智能搜索..." : "正在搜索...";
      resultsContent.innerHTML = `<div class="text-center py-5"><div class="spinner-border text-primary" role="status"></div><p class="mt-3">${loadingText}</p></div>`;
      // searchKeyword.textContent = `"${query}"`; // 删除对 searchKeyword 的依赖

      // 显示搜索结果区域，隐藏分类容器
      categoriesContainer.style.display = "none";
      searchResults.style.display = "block";
      searchResults.style.opacity = "1"; // 确保搜索结果区域可见
      noResults.style.display = "none";

      // 构建搜索URL
      let searchUrl = `/api/search?q=${encodeURIComponent(query)}`;
      if (useAI) {
        searchUrl += "&ai=true&progressive=true"; // 启用渐进式返回
      }
      console.log("搜索URL:", searchUrl);

      // 渐进式搜索（AI搜索时使用）
      if (useAI) {
        console.log("使用渐进式搜索");
        _progressiveSearch(searchUrl, query);
      } else {
        // 传统搜索（一次性返回）
        console.log("使用传统搜索");
        _traditionalSearch(searchUrl, query);
      }
    } else {
      clearSearch();
    }
  });

  // 渐进式搜索函数
  function _progressiveSearch(searchUrl, query) {
    // EventSource 不支持自定义请求头，如果失败则回退到传统搜索
    try {
      const eventSource = new EventSource(searchUrl);
      let currentWebsites = [];
      let searchSummary = document.getElementById("searchSummary");
      let hasReceivedData = false;

      eventSource.onmessage = function (event) {
        try {
          hasReceivedData = true;

          // EventSource 返回的数据格式是 "data: {...}\n\n"，需要提取 data 部分
          let dataStr = event.data;
          if (dataStr.startsWith("data: ")) {
            dataStr = dataStr.substring(6); // 移除 "data: " 前缀
          }

          const data = JSON.parse(dataStr);

          if (data.stage === "error") {
            eventSource.close();
            resultsContent.innerHTML =
              '<div class="text-center py-5 text-muted"><i class="bi bi-exclamation-circle fs-1"></i><p class="mt-3">搜索过程中发生错误: ' +
              (data.error || "未知错误") +
              "</p></div>";
            return;
          }

          // 更新搜索结果（增量更新，不清空已有内容）
          currentWebsites = data.websites || [];

          // 获取已存在的网站ID集合
          const existingIds = new Set(
            Array.from(resultsContent.querySelectorAll(".site-card")).map(
              (card) => parseInt(card.dataset.id)
            )
          );

          // 判断是否有已显示的结果
          const hasExistingResults = existingIds.size > 0;

          // 如果是初始阶段
          if (data.stage === "initial") {
            // 如果有结果，直接渲染；如果为空，显示加载状态
            if (currentWebsites.length > 0) {
              _renderWebsites(currentWebsites);
            } else {
              // 第一阶段为空，显示加载提示，等待后续阶段
              resultsContent.innerHTML = `<div class="text-center py-5"><div class="spinner-border text-primary" role="status"></div><p class="mt-3">正在使用AI智能搜索...</p></div>`;
            }
          }
          // 如果是增强阶段
          else if (data.stage === "enhanced") {
            // 如果之前没有结果（第一阶段为空），直接渲染所有结果
            if (!hasExistingResults && currentWebsites.length > 0) {
              _renderWebsites(currentWebsites);
            }
            // 如果之前有结果，检查是否有新增
            else if (hasExistingResults) {
              // 重新获取已存在的ID（因为DOM可能已经更新）
              const currentExistingIds = new Set(
                Array.from(resultsContent.querySelectorAll(".site-card")).map(
                  (card) => parseInt(card.dataset.id)
                )
              );

              const newWebsites = currentWebsites.filter(
                (site) => !currentExistingIds.has(site.id)
              );

              // 如果有新网站，增量添加（这是渐进式渲染的核心）
              if (newWebsites.length > 0) {
                _appendWebsites(newWebsites);
              }
              // 如果没有新网站，说明这批是累积结果但所有网站都已显示
              // 这种情况下不需要做任何操作，保持当前显示状态
            }
          }
          // 如果是最终阶段（AI排序完成）
          else if (data.stage === "final") {
            // 最终阶段：智能更新顺序，不清空重绘
            if (currentWebsites.length > 0) {
              // 检查顺序是否真的改变了
              const currentIds = Array.from(
                resultsContent.querySelectorAll(".site-card")
              ).map((card) => parseInt(card.dataset.id));
              const newIds = currentWebsites.map((site) => site.id);

              // 如果顺序相同，只更新状态；如果顺序不同，重新排序渲染
              if (
                currentIds.length === newIds.length &&
                currentIds === newIds
              ) {
                // 顺序没变，只更新状态提示
                if (searchSummary) {
                  let summaryText = `找到 <strong>${
                    data.total || 0
                  }</strong> 个与 <span class="search-keyword">"${query}"</span> 相关的网站`;
                  if (data.ai_enabled) {
                    summaryText += ` <span class="badge bg-primary ms-2" style="font-size: 0.75rem;"><i class="bi bi-robot me-1"></i>AI智能搜索</span>`;
                  }
                  if (data.ai_summary) {
                    summaryText += `<div class="mt-2 text-muted" style="font-size: 0.9rem;"><i class="bi bi-lightbulb me-1"></i>${data.ai_summary}</div>`;
                  }
                  if (data.status) {
                    summaryText += `<div class="mt-2 text-muted" style="font-size: 0.85rem;"><i class="bi bi-info-circle me-1"></i>${data.status}</div>`;
                  }
                  searchSummary.innerHTML = summaryText;
                }
              } else {
                // 顺序改变了，重新渲染（AI排序后的顺序）
                _renderWebsites(currentWebsites);
              }
            } else if (!hasExistingResults) {
              // 如果所有阶段都没有结果
              resultsContent.innerHTML =
                '<div class="text-center py-5 text-muted"><i class="bi bi-search fs-1"></i><p class="mt-3">未找到相关网站</p></div>';
            }
          }

          // 更新状态提示
          if (searchSummary) {
            let summaryText = `找到 <strong>${
              data.total || 0
            }</strong> 个与 <span class="search-keyword">"${query}"</span> 相关的网站`;
            if (data.ai_enabled) {
              summaryText += ` <span class="badge bg-primary ms-2" style="font-size: 0.75rem;"><i class="bi bi-robot me-1"></i>AI智能搜索</span>`;
            }
            if (data.status) {
              summaryText += `<div class="mt-2 text-muted" style="font-size: 0.85rem;"><i class="bi bi-info-circle me-1"></i>${data.status}</div>`;
            }
            if (data.ai_summary) {
              summaryText += `<div class="mt-2 text-muted" style="font-size: 0.9rem;"><i class="bi bi-lightbulb me-1"></i>${data.ai_summary}</div>`;
            }
            searchSummary.innerHTML = summaryText;
          }

          // 如果是最终阶段，关闭连接
          if (data.stage === "final") {
            eventSource.close();
          }
        } catch (e) {
          console.error("解析渐进式搜索结果失败:", e, event.data);
        }
      };

      eventSource.onerror = function (event) {
        eventSource.close();
        // 如果出错且没有收到任何数据，尝试传统搜索
        if (!hasReceivedData) {
          _traditionalSearch(searchUrl.replace("&progressive=true", ""), query);
        }
      };

      // 设置超时，如果5秒内没有收到数据，回退到传统搜索
      setTimeout(function () {
        if (!hasReceivedData && eventSource.readyState !== EventSource.CLOSED) {
          eventSource.close();
          _traditionalSearch(searchUrl.replace("&progressive=true", ""), query);
        }
      }, 5000);
    } catch (e) {
      _traditionalSearch(searchUrl.replace("&progressive=true", ""), query);
    }
  }

  // 传统搜索函数
  function _traditionalSearch(searchUrl, query) {
    console.log("执行传统搜索:", searchUrl);
    fetch(searchUrl)
      .then((response) => {
        console.log("搜索响应状态:", response.status);
        if (!response.ok) {
          throw new Error(`HTTP错误: ${response.status}`);
        }
        return response.json();
      })
      .then((data) => {
        console.log("搜索返回数据:", data);
        // 清空之前的搜索结果
        resultsContent.innerHTML = "";

        // 设置数量提示到毛玻璃卡片内
        const searchSummary = document.getElementById("searchSummary");
        if (searchSummary) {
          let summaryText = `找到 <strong>${
            data.total || data.count || 0
          }</strong> 个与 <span class="search-keyword">"${query}"</span> 相关的网站`;
          if (data.ai_enabled) {
            summaryText += ` <span class="badge bg-primary ms-2" style="font-size: 0.75rem;"><i class="bi bi-robot me-1"></i>AI智能搜索</span>`;
          }
          if (data.ai_summary) {
            summaryText += `<div class="mt-2 text-muted" style="font-size: 0.9rem;"><i class="bi bi-lightbulb me-1"></i>${data.ai_summary}</div>`;
          }
          searchSummary.innerHTML = summaryText;
        }

        _renderWebsites(data.websites || []);
      })
      .catch((error) => {
        console.error("搜索出错:", error);
        resultsContent.innerHTML =
          '<div class="text-center py-5 text-muted"><i class="bi bi-exclamation-circle fs-1"></i><p class="mt-3">搜索过程中发生错误: ' +
          error.message +
          "</p></div>";
      });
  }

  // 渲染网站列表（通用函数）
  function _renderWebsites(websites) {
    // 清空之前的搜索结果
    resultsContent.innerHTML = "";

    if (websites && websites.length > 0) {
      // 创建卡片容器
      const cardContainer = document.createElement("div");
      cardContainer.className = "card-container";
      resultsContent.appendChild(cardContainer);

      // 循环添加搜索结果
      websites.forEach((site) => {
        // 创建卡片元素
        const siteCard = document.createElement("a");
        siteCard.href = `/site/${site.id}`;
        siteCard.className = "site-card";
        siteCard.dataset.id = site.id;
        siteCard.title = site.description || "";
        siteCard.dataset.bsToggle = "tooltip";
        siteCard.dataset.bsPlacement = "bottom";
        siteCard.target = "_blank"; // 添加新标签页打开属性

        // 添加私有标记
        if (site.is_private) {
          const privateBadge = document.createElement("div");
          privateBadge.className = "private-badge";
          privateBadge.title = "私有链接";
          privateBadge.innerHTML = '<i class="bi bi-lock-fill"></i>';
          siteCard.appendChild(privateBadge);
        }

        // 创建网站卡片内容结构
        const siteHeader = document.createElement("div");
        siteHeader.className = "site-header";

        // 创建图标容器
        const iconContainer = document.createElement("div");
        iconContainer.className = "site-icon";

        if (site.icon) {
          const img = document.createElement("img");
          img.src = site.icon;
          img.alt = site.title;
          iconContainer.appendChild(img);
        } else {
          // 使用网站标题首字母作为默认图标
          const defaultIcon = document.createElement("div");
          defaultIcon.className = "default-site-icon";
          defaultIcon.textContent = site.title.charAt(0).toUpperCase();
          iconContainer.appendChild(defaultIcon);
        }

        // 创建文本容器
        const textContainer = document.createElement("div");
        textContainer.className = "site-text";

        const titleEl = document.createElement("h5");
        titleEl.className = "site-title";
        titleEl.textContent = site.title;
        textContainer.appendChild(titleEl);

        const descEl = document.createElement("p");
        descEl.className = "site-description";
        descEl.textContent = site.description || "";
        textContainer.appendChild(descEl);

        // 组装卡片结构
        siteHeader.appendChild(iconContainer);
        siteHeader.appendChild(textContainer);
        siteCard.appendChild(siteHeader);

        // 将卡片添加到结果容器
        cardContainer.appendChild(siteCard);
      });

      // 初始化工具提示
      if (typeof bootstrap !== "undefined") {
        const tooltipTriggerList = [].slice.call(
          resultsContent.querySelectorAll('[data-bs-toggle="tooltip"]')
        );
        tooltipTriggerList.map(function (tooltipTriggerEl) {
          return new bootstrap.Tooltip(tooltipTriggerEl);
        });
      }
    } else {
      resultsContent.innerHTML =
        '<div class="text-center py-5 text-muted"><i class="bi bi-search fs-1"></i><p class="mt-3">未找到相关网站</p></div>';
    }
  }

  // 增量添加网站（用于渐进式搜索）- 逐个添加，实现真正的渐进式效果
  function _appendWebsites(websites) {
    if (!websites || websites.length === 0) {
      return;
    }

    // 获取或创建卡片容器
    let cardContainer = resultsContent.querySelector(".card-container");
    if (!cardContainer) {
      cardContainer = document.createElement("div");
      cardContainer.className = "card-container";
      resultsContent.appendChild(cardContainer);
    }

    // 获取已存在的网站ID集合（用于去重）
    const existingIds = new Set(
      Array.from(cardContainer.querySelectorAll(".site-card")).map((card) =>
        parseInt(card.dataset.id)
      )
    );

    // 过滤出真正的新网站
    const newWebsites = websites.filter((site) => !existingIds.has(site.id));

    if (newWebsites.length === 0) {
      return; // 没有新网站，直接返回
    }

    // 逐个添加网站卡片，每个卡片之间有延迟，实现流畅的渐进式效果
    newWebsites.forEach((site, index) => {
      // 每个卡片延迟 80ms，形成流畅的渐进式出现效果
      setTimeout(() => {
        // 再次检查是否已存在（防止重复添加）
        const currentExistingIds = new Set(
          Array.from(cardContainer.querySelectorAll(".site-card")).map((card) =>
            parseInt(card.dataset.id)
          )
        );

        if (currentExistingIds.has(site.id)) {
          return; // 已存在，跳过
        }

        // 创建卡片元素
        const siteCard = document.createElement("a");
        siteCard.href = `/site/${site.id}`;
        siteCard.className = "site-card";
        siteCard.dataset.id = site.id;
        siteCard.title = site.description || "";
        siteCard.dataset.bsToggle = "tooltip";
        siteCard.dataset.bsPlacement = "bottom";
        siteCard.target = "_blank";

        if (site.is_private) {
          const privateBadge = document.createElement("div");
          privateBadge.className = "private-badge";
          privateBadge.title = "私有链接";
          privateBadge.innerHTML = '<i class="bi bi-lock-fill"></i>';
          siteCard.appendChild(privateBadge);
        }

        const siteHeader = document.createElement("div");
        siteHeader.className = "site-header";

        const iconContainer = document.createElement("div");
        iconContainer.className = "site-icon";

        if (site.icon) {
          const img = document.createElement("img");
          img.src = site.icon;
          img.alt = site.title;
          iconContainer.appendChild(img);
        } else {
          const defaultIcon = document.createElement("div");
          defaultIcon.className = "default-site-icon";
          defaultIcon.textContent = site.title.charAt(0).toUpperCase();
          iconContainer.appendChild(defaultIcon);
        }

        const textContainer = document.createElement("div");
        textContainer.className = "site-text";

        const titleEl = document.createElement("h5");
        titleEl.className = "site-title";
        titleEl.textContent = site.title;
        textContainer.appendChild(titleEl);

        const descEl = document.createElement("p");
        descEl.className = "site-description";
        descEl.textContent = site.description || "";
        textContainer.appendChild(descEl);

        siteHeader.appendChild(iconContainer);
        siteHeader.appendChild(textContainer);
        siteCard.appendChild(siteHeader);

        // 使用动画效果添加新卡片
        siteCard.style.opacity = "0";
        siteCard.style.transform = "translateY(10px)";
        cardContainer.appendChild(siteCard);

        // 触发淡入和上移动画
        setTimeout(() => {
          siteCard.style.transition = "opacity 0.3s ease, transform 0.3s ease";
          siteCard.style.opacity = "1";
          siteCard.style.transform = "translateY(0)";
        }, 10);

        // 初始化工具提示
        if (typeof bootstrap !== "undefined") {
          new bootstrap.Tooltip(siteCard);
        }
      }, index * 80); // 每个卡片延迟 80ms，形成流畅的渐进式效果
    });
  }

  // 监听搜索框输入
  searchInput.addEventListener("input", function () {
    if (this.value.trim()) {
      clearSearchBtn.style.display = "flex";
    } else {
      clearSearchBtn.style.display = "none";
      // 如果输入框被清空，自动恢复显示原始内容
      if (searchResults.style.display !== "none") {
        clearSearch();
      }
    }
  });

  // 检查初始状态下是否应该显示清除按钮
  function checkClearButtonVisibility() {
    if (searchInput.value.trim()) {
      clearSearchBtn.style.display = "flex";
    } else {
      clearSearchBtn.style.display = "none";
    }
  }

  // 页面加载时和焦点改变时检查
  checkClearButtonVisibility();
  searchInput.addEventListener("focus", checkClearButtonVisibility);

  // 清除搜索按钮
  clearSearchBtn.addEventListener("click", clearSearch);

  // 清除搜索
  function clearSearch() {
    searchInput.value = "";
    clearSearchBtn.style.display = "none";
    searchResults.style.display = "none";
    categoriesContainer.style.display = "block";
    categoriesContainer.style.opacity = "1"; // 确保分类容器可见
  }

  // 监听清除搜索事件
  window.addEventListener("clearSearch", clearSearch);
});
