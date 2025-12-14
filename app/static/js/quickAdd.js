document.addEventListener("paste", async function (e) {
  // 只有管理员才能使用快速添加功能
  if (!document.body.classList.contains("user-admin")) {
    return;
  }

  // 如果编辑链接窗口打开中，不触发快速添加
  const editLinkModal = document.getElementById("editLinkModal");
  if (editLinkModal && editLinkModal.style.display === "flex") {
    return;
  }

  // 检查当前是否有输入框正在获取焦点
  const activeElement = document.activeElement;
  const isInputElement =
    activeElement.tagName === "INPUT" ||
    activeElement.tagName === "TEXTAREA" ||
    activeElement.isContentEditable;

  // 如果当前焦点在输入框，不触发快速添加功能
  if (isInputElement) {
    return;
  }

  // 如果快速添加窗口打开中，也不再触发新的快速添加
  const quickAddModal = document.getElementById("quickAddModal");
  if (quickAddModal && quickAddModal.style.display === "flex") {
    return;
  }

  // 获取剪贴板内容
  const clipboardData = e.clipboardData || window.clipboardData;
  const pastedData = clipboardData.getData("text");

  // 验证是否是有效的URL
  if (!isValidUrl(pastedData)) {
    return;
  }

  // 显示加载中状态
  showQuickAddModal();
  setQuickAddLoading(true, "正在解析粘贴的链接...");

  try {
    // 检查URL是否已存在
    updateQuickAddProgress("正在检查URL是否已存在...", 10);

    const checkResponse = await fetch(
      `/api/check_url_exists?url=${encodeURIComponent(pastedData)}`
    );
    const checkResult = await checkResponse.json();

    if (checkResult.exists) {
      // 使用自定义对话框替代confirm
      closeQuickAddModal();

      // 显示重复链接提示对话框
      const action = await showDuplicateLinkPrompt(checkResult.website);

      if (action === "view") {
        // 导航到已有链接
        navigateToExistingLink(checkResult.website);
        return;
      } else if (action === "cancel") {
        // 用户选择取消添加
        return;
      }
      // 用户选择继续添加，重新打开快速添加对话框
      showQuickAddModal();
      setQuickAddLoading(true, "继续添加链接...");
    }

    // 检查是否支持流式响应
    const supportsStreaming =
      "ReadableStream" in window &&
      "getReader" in window.ReadableStream.prototype;

    if (supportsStreaming) {
      // 使用流式API获取网站信息
      await fetchWithProgress(pastedData);
    } else {
      // 使用传统方式获取网站信息
      await fetchWebsiteInfoTraditional(pastedData);
    }
  } catch (error) {
    console.error("获取网站信息失败:", error);
    // 如果获取失败，至少填充URL
    document.getElementById("quickAddUrl").value = pastedData;

    // 尝试使用AI填充
    try {
      updateQuickAddProgress("正在使用AI生成网站信息...", 90);

      const requestOptions = {
        method: "GET",
        redirect: "follow",
      };

      const aiResult = await fetch(
        `/api/fetch_website_info?url=${encodeURIComponent(
          pastedData
        )}&use_ai_fallback=true`,
        requestOptions
      ).then((r) => r.json());

      // 如果AI成功生成，填充表单
      if (aiResult.success) {
        document.getElementById("quickAddTitle").value = aiResult.title || "";
        document.getElementById("quickAddDescription").value =
          aiResult.description || "";

        if (aiResult.icon_url) {
          const iconInput = document.getElementById("quickAddIcon");
          const iconPreview = document.getElementById("quickAddIconPreview");
          iconInput.value = aiResult.icon_url;
          iconPreview.src = aiResult.icon_url;
          iconPreview.setAttribute("referrerpolicy", "no-referrer");
          iconPreview.style.display = "block";
        }

        updateQuickAddProgress("AI生成完成", 100);
        showTemporaryNotification(
          "网站信息获取失败，已使用AI生成（请检查并调整）",
          "warning"
        );
        setQuickAddLoading(false);
        return;
      }
    } catch (aiError) {
      console.warn("AI生成也失败:", aiError);
    }

    // 提供错误类型信息
    let errorMessage = "获取网站信息失败";
    if (error.name === "AbortError") {
      errorMessage += "(请求超时)";
    } else if (
      error.name === "TypeError" &&
      error.message.includes("networkerror")
    ) {
      errorMessage += "(网络错误)";
    } else if (error.message) {
      errorMessage += `(${error.message})`;
    }

    showTemporaryNotification(`${errorMessage}，请手动填写`, "error");
  } finally {
    setQuickAddLoading(false);
  }
});

/**
 * 使用流式响应获取网站信息（带进度）
 * @param {string} url - 网站URL
 */
async function fetchWithProgress(url) {
  updateQuickAddProgress("正在获取网站详细信息...", 20);

  // 创建AbortController用于可能的请求取消
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000); // 30秒超时

  try {
    const response = await fetch(
      `/api/fetch_website_info_with_progress?url=${encodeURIComponent(url)}`,
      {
        signal: controller.signal,
        headers: {
          Accept: "text/event-stream",
        },
      }
    );

    // 检查响应是否成功
    if (!response.ok) {
      throw new Error(`HTTP错误: ${response.status}`);
    }

    // 获取响应体的reader
    const reader = response.body.getReader();
    let decoder = new TextDecoder();
    let buffer = "";
    let result = null;

    // 循环读取直到数据完成
    while (true) {
      const { value, done } = await reader.read();

      if (done) break;

      // 解码收到的数据并添加到缓冲区
      const chunk = decoder.decode(value, { stream: true });
      buffer += chunk;

      // 处理缓冲区中的所有完整行
      let newlineIndex;
      while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);

        if (line.trim() === "") continue; // 跳过空行

        try {
          const event = JSON.parse(line);

          // 更新进度显示
          updateQuickAddProgress(event.message, event.progress);

          // 如果是最终结果，保存结果
          if (event.stage === "complete" && event.success) {
            result = event;
          } else if (event.stage === "error") {
            throw new Error(event.message);
          }
        } catch (e) {
          console.error("解析进度数据出错:", e, line);
        }
      }
    }

    // 如果有剩余数据，尝试解析
    if (buffer && !result) {
      try {
        const lastEvent = JSON.parse(buffer);

        // 如果是最终结果，保存结果
        if (lastEvent.stage === "complete" && lastEvent.success) {
          result = lastEvent;
        } else if (lastEvent.stage === "error") {
          throw new Error(lastEvent.message);
        }
      } catch (e) {
        console.error("解析最终数据出错:", e);
      }
    }

    // 如果没有获取到结果，尝试使用AI填充
    if (!result) {
      updateQuickAddProgress("正在使用AI生成网站信息...", 85);

      try {
        const requestOptions = {
          method: "GET",
          redirect: "follow",
        };

        const aiResult = await fetch(
          `/api/fetch_website_info?url=${encodeURIComponent(
            url
          )}&use_ai_fallback=true`,
          requestOptions
        ).then((r) => r.json());

        // 如果AI成功生成，使用AI结果
        if (aiResult.success) {
          result = {
            success: true,
            title: aiResult.title || "",
            description: aiResult.description || "",
            icon_url: aiResult.icon_url || "",
            domain: aiResult.domain || "",
            ai_generated_title: !!aiResult.title,
            ai_generated_description: !!aiResult.description,
          };
        } else {
          // AI也失败了，抛出错误
          throw new Error("未收到完整的网站信息，AI生成也失败");
        }
      } catch (error) {
        console.warn("AI生成网站信息失败:", error);
        throw new Error("未收到完整的网站信息");
      }
    }

    // 如果获取失败或信息不完整，尝试使用AI填充
    const hasTitleSuccess = result.title && result.title.trim() !== "";
    const hasDescSuccess =
      result.description && result.description.trim() !== "";

    if (!result.success || !hasTitleSuccess || !hasDescSuccess) {
      updateQuickAddProgress("正在使用AI生成网站信息...", 85);

      try {
        const requestOptions = {
          method: "GET",
          redirect: "follow",
        };

        const aiResult = await fetch(
          `/api/fetch_website_info?url=${encodeURIComponent(
            url
          )}&use_ai_fallback=true`,
          requestOptions
        ).then((r) => r.json());

        // 如果AI成功生成，使用AI结果
        if (aiResult.success) {
          if (!hasTitleSuccess && aiResult.title) {
            result.title = aiResult.title;
            result.ai_generated_title = true;
          }
          if (!hasDescSuccess && aiResult.description) {
            result.description = aiResult.description;
            result.ai_generated_description = true;
          }
          // 如果result.success为false，但AI生成了内容，更新success状态
          if (!result.success && (aiResult.title || aiResult.description)) {
            result.success = true;
          }
        }
      } catch (error) {
        console.warn("AI生成网站信息失败:", error);
      }
    }

    // 填充表单
    document.getElementById("quickAddTitle").value = result.title || "";
    document.getElementById("quickAddUrl").value = url;
    document.getElementById("quickAddDescription").value =
      result.description || "";

    // 设置图标
    if (result.icon_url) {
      const iconInput = document.getElementById("quickAddIcon");
      const iconPreview = document.getElementById("quickAddIconPreview");

      iconInput.value = result.icon_url;
      iconPreview.src = result.icon_url;
      iconPreview.setAttribute("referrerpolicy", "no-referrer");
      iconPreview.style.display = "block";
    }

    // 更新获取情况
    const finalHasTitleSuccess = result.title && result.title.trim() !== "";
    const finalHasDescSuccess =
      result.description && result.description.trim() !== "";
    const hasIconSuccess = result.icon_url && result.icon_url.trim() !== "";

    // 根据获取结果提供精确的反馈
    if (finalHasTitleSuccess && finalHasDescSuccess && hasIconSuccess) {
      // 全部成功
      let successMsg = "网站信息获取成功";
      if (result.ai_generated_title || result.ai_generated_description) {
        successMsg += "（部分信息由AI生成）";
      }
      showTemporaryNotification(successMsg, "success");
    } else {
      // 部分失败
      let missingParts = [];
      if (!finalHasTitleSuccess) missingParts.push("标题");
      if (!finalHasDescSuccess) missingParts.push("描述");
      if (!hasIconSuccess) missingParts.push("图标");

      let warningMsg = `${missingParts.join("、")}获取失败`;
      if (result.ai_generated_title || result.ai_generated_description) {
        warningMsg += "（已尝试AI生成）";
      }
      warningMsg += "，请手动补充";
      showTemporaryNotification(warningMsg, "warning");
    }
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * 使用传统方式获取网站信息
 * @param {string} url - 网站URL
 */
async function fetchWebsiteInfoTraditional(url) {
  const supportsDetailedProgress = isDetailedProgressSupported();

  updateQuickAddProgress("正在获取网站信息...", 30);

  // 获取网站信息和图标
  const requestOptions = {
    method: "GET",
    redirect: "follow",
  };

  let websiteInfo = await fetch(
    `/api/fetch_website_info?url=${encodeURIComponent(url)}`,
    requestOptions
  ).then((r) => r.json());

  // 如果获取失败或信息不完整，尝试使用AI填充
  const hasTitleSuccess = websiteInfo.title && websiteInfo.title.trim() !== "";
  const hasDescSuccess =
    websiteInfo.description && websiteInfo.description.trim() !== "";

  // 无论是否支持详细进度，都尝试AI填充
  if (!websiteInfo.success || !hasTitleSuccess || !hasDescSuccess) {
    if (supportsDetailedProgress) {
      updateQuickAddProgress("正在使用AI生成网站信息...", 60);
    }

    try {
      const aiResult = await fetch(
        `/api/fetch_website_info?url=${encodeURIComponent(
          url
        )}&use_ai_fallback=true`,
        requestOptions
      ).then((r) => r.json());

      // 如果AI成功生成，使用AI结果
      if (aiResult.success) {
        if (!hasTitleSuccess && aiResult.title) {
          websiteInfo.title = aiResult.title;
          websiteInfo.ai_generated_title = true;
        }
        if (!hasDescSuccess && aiResult.description) {
          websiteInfo.description = aiResult.description;
          websiteInfo.ai_generated_description = true;
        }
        // 如果原来失败，但AI生成了内容，更新success状态
        if (!websiteInfo.success && (aiResult.title || aiResult.description)) {
          websiteInfo.success = true;
        }
      }
    } catch (error) {
      console.warn("AI生成网站信息失败:", error);
    }
  }

  if (supportsDetailedProgress) {
    updateQuickAddProgress("正在解析网站标题和描述...", 70);
  }

  // 填充表单
  document.getElementById("quickAddTitle").value = websiteInfo.title || "";
  document.getElementById("quickAddUrl").value = url;
  document.getElementById("quickAddDescription").value =
    websiteInfo.description || "";

  // 更新获取情况
  const finalHasTitleSuccess =
    websiteInfo.title && websiteInfo.title.trim() !== "";
  const finalHasDescSuccess =
    websiteInfo.description && websiteInfo.description.trim() !== "";
  const hasIconSuccess =
    websiteInfo.icon_url && websiteInfo.icon_url.trim() !== "";

  if (supportsDetailedProgress) {
    updateQuickAddProgress("正在获取网站图标...", 80);
  }

  // 设置图标
  if (hasIconSuccess) {
    const iconInput = document.getElementById("quickAddIcon");
    const iconPreview = document.getElementById("quickAddIconPreview");
    iconInput.value = websiteInfo.icon_url;
    iconPreview.src = websiteInfo.icon_url;
    iconPreview.setAttribute("referrerpolicy", "no-referrer");
    iconPreview.style.display = "block";
  }

  if (supportsDetailedProgress) {
    updateQuickAddProgress("网站信息获取完成", 100);
  }

  // 根据获取结果提供精确的反馈
  if (finalHasTitleSuccess && finalHasDescSuccess && hasIconSuccess) {
    // 全部成功
    let successMsg = "网站信息获取成功";
    if (
      websiteInfo.ai_generated_title ||
      websiteInfo.ai_generated_description
    ) {
      successMsg += "（部分信息由AI生成）";
    }
    showTemporaryNotification(successMsg, "success");
  } else if (!websiteInfo.success) {
    // API返回失败
    let failReason = "";
    if (websiteInfo.message) {
      // 有错误信息
      if (websiteInfo.message.includes("timeout")) {
        failReason = "请求超时";
      } else if (
        websiteInfo.message.includes("403") ||
        websiteInfo.message.includes("forbidden")
      ) {
        failReason = "网站禁止访问";
      } else {
        failReason = websiteInfo.message;
      }
    } else {
      failReason = "未知原因";
    }
    showTemporaryNotification(`获取失败(${failReason})，请手动填写`, "error");
  } else {
    // 部分失败
    let missingParts = [];
    if (!finalHasTitleSuccess) missingParts.push("标题");
    if (!finalHasDescSuccess) missingParts.push("描述");
    if (!hasIconSuccess) missingParts.push("图标");

    let warningMsg = `${missingParts.join("、")}获取失败`;
    if (
      websiteInfo.ai_generated_title ||
      websiteInfo.ai_generated_description
    ) {
      warningMsg += "（已尝试AI生成）";
    }
    warningMsg += "，请手动补充";
    showTemporaryNotification(warningMsg, "warning");
  }
}

// 验证URL是否合法
function isValidUrl(url) {
  try {
    const urlObj = new URL(url);
    return urlObj.protocol === "http:" || urlObj.protocol === "https:";
  } catch (error) {
    return false;
  }
}

// 显示临时通知
function showTemporaryNotification(message, type = "info") {
  // 检查是否已有通知元素
  let notification = document.getElementById("quickAddNotification");
  if (!notification) {
    // 创建通知元素
    notification = document.createElement("div");
    notification.id = "quickAddNotification";
    notification.className = "quick-add-notification";
    document.body.appendChild(notification);
  }

  // 设置通知类型样式
  notification.className = `quick-add-notification ${type}`;
  notification.innerHTML = `<i class="bi bi-${
    type === "success"
      ? "check-circle"
      : type === "warning"
      ? "exclamation-triangle"
      : "info-circle"
  } me-2"></i>${message}`;

  // 显示通知
  notification.style.display = "block";

  // 3秒后自动隐藏
  setTimeout(() => {
    notification.style.opacity = "0";
    setTimeout(() => {
      notification.style.display = "none";
      notification.style.opacity = "1";
    }, 300);
  }, 3000);
}

function showQuickAddModal() {
  const modal = document.getElementById("quickAddModal");
  modal.style.display = "flex";
}

function closeQuickAddModal() {
  const modal = document.getElementById("quickAddModal");
  modal.style.display = "none";
  // 清空表单
  document.getElementById("quickAddTitle").value = "";
  document.getElementById("quickAddUrl").value = "";
  document.getElementById("quickAddDescription").value = "";
  document.getElementById("quickAddIcon").value = "";
  document.getElementById("quickAddIconPreview").style.display = "none";
  document.getElementById("quickAddCategory").value = "";
  document.getElementById("quickAddWeight").value = "0"; // 重置权重为默认值
}

function setQuickAddLoading(isLoading, message = "加载中...") {
  const submitBtn = document.querySelector("#quickAddModal .btn-primary");
  if (isLoading) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${message}`;

    // 显示加载覆盖层
    const loadingOverlay = document.getElementById("quickAddLoadingOverlay");
    if (loadingOverlay) {
      const textElement = loadingOverlay.querySelector(".form-loading-text");
      if (textElement) {
        textElement.textContent = message;
      }
      loadingOverlay.classList.add("show");
    }
  } else {
    submitBtn.disabled = false;
    submitBtn.innerHTML = '<i class="bi bi-plus-lg me-1"></i>添加';

    // 隐藏加载覆盖层
    const loadingOverlay = document.getElementById("quickAddLoadingOverlay");
    if (loadingOverlay) {
      loadingOverlay.classList.remove("show");
    }
  }
}

async function submitQuickAdd() {
  const categoryId = document.getElementById("quickAddCategory").value;
  if (!categoryId) {
    alert("请选择分类");
    return;
  }

  const url = document.getElementById("quickAddUrl").value.trim();

  try {
    // 检查URL是否已存在（这里是新增，不需要排除ID）
    const checkResponse = await fetch(
      `/api/check_url_exists?url=${encodeURIComponent(url)}`
    );
    const checkResult = await checkResponse.json();

    if (checkResult.exists) {
      // 使用自定义对话框替代confirm
      const action = await showDuplicateLinkPrompt(checkResult.website);

      if (action === "view") {
        // 导航到已有链接
        navigateToExistingLink(checkResult.website);
        return;
      } else if (action === "cancel") {
        // 用户选择取消添加
        return;
      }
      // 用户选择继续添加
    }

    const submitBtn = document.querySelector("#quickAddModal .btn-primary");
    submitBtn.disabled = true;
    submitBtn.innerHTML =
      '<span class="spinner-border spinner-border-sm me-1"></span>提交中...';

    const data = {
      title: document.getElementById("quickAddTitle").value.trim(),
      url: url,
      description: document.getElementById("quickAddDescription").value.trim(),
      icon: document.getElementById("quickAddIcon").value.trim(),
      category_id: parseInt(categoryId),
      is_private: document.getElementById("quickAddPrivate").checked ? 1 : 0,
      sort_order:
        parseInt(document.getElementById("quickAddWeight").value) || 0,
    };

    const response = await fetch("/api/website/quick-add", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')
          .content,
      },
      body: JSON.stringify(data),
    });

    const result = await response.json();
    if (result.success) {
      showTemporaryNotification("网站添加成功！", "success");
      closeQuickAddModal();
      // 刷新页面以显示新添加的链接
      setTimeout(() => {
        window.location.reload();
      }, 1000); // 延迟1秒刷新，让用户看到成功通知
    } else {
      showTemporaryNotification(result.message || "添加失败", "error");
    }
  } catch (error) {
    console.error("提交失败:", error);
    showTemporaryNotification("提交失败，请重试", "error");
  } finally {
    const submitBtn = document.querySelector("#quickAddModal .btn-primary");
    submitBtn.disabled = false;
    submitBtn.innerHTML = '<i class="bi bi-plus-lg me-1"></i>添加';
  }
}

/**
 * 检查是否支持详细进度展示
 * @returns {boolean}
 */
function isDetailedProgressSupported() {
  // 这里可以根据需要添加更多检查，例如浏览器版本等
  return true;
}

/**
 * 更新快速添加对话框的加载进度信息
 * @param {string} message - 进度信息文本
 * @param {number} percent - 进度百分比(0-100)
 */
function updateQuickAddProgress(message, percent = null) {
  const submitBtn = document.querySelector("#quickAddModal .btn-primary");
  let loadingText = message;

  // 如果提供了百分比，则显示百分比
  if (percent !== null) {
    loadingText += ` (${percent}%)`;
  }

  submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${loadingText}`;

  // 同时更新覆盖层文本，如果存在的话
  const loadingOverlay = document.getElementById("quickAddLoadingOverlay");
  if (loadingOverlay) {
    const textElement = loadingOverlay.querySelector(".form-loading-text");
    if (textElement) {
      textElement.textContent = loadingText;
    }
  }
}

/**
 * 翻译描述文本为中文
 * @param {string} descriptionId - 描述输入框的ID
 * @param {string} buttonId - 翻译按钮的ID
 */
async function translateDescription(descriptionId, buttonId) {
  const descInput = document.getElementById(descriptionId);
  const translateBtn = document.getElementById(buttonId);

  if (!descInput || !translateBtn) {
    return;
  }

  const originalText = descInput.value.trim();

  if (!originalText) {
    showTemporaryNotification("请先输入要翻译的文本", "warning");
    return;
  }

  // 禁用按钮并显示加载状态
  translateBtn.disabled = true;
  const originalHtml = translateBtn.innerHTML;
  translateBtn.innerHTML =
    '<span class="spinner-border spinner-border-sm"></span>';

  try {
    const response = await fetch("/api/translate_description", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken":
          document.querySelector('meta[name="csrf-token"]')?.content || "",
      },
      body: JSON.stringify({ text: originalText }),
    });

    const result = await response.json();

    if (result.success) {
      descInput.value = result.translated_text;
      showTemporaryNotification("翻译成功", "success");
    } else {
      showTemporaryNotification(result.message || "翻译失败", "error");
    }
  } catch (error) {
    console.error("翻译失败:", error);
    showTemporaryNotification("翻译失败，请稍后重试", "error");
  } finally {
    // 恢复按钮状态
    translateBtn.disabled = false;
    translateBtn.innerHTML = originalHtml;
  }
}

// 绑定快速添加表单的翻译按钮
document.addEventListener("DOMContentLoaded", function () {
  const quickAddTranslateBtn = document.getElementById("quickAddTranslateBtn");
  if (quickAddTranslateBtn) {
    quickAddTranslateBtn.addEventListener("click", function () {
      translateDescription("quickAddDescription", "quickAddTranslateBtn");
    });
  }
});
