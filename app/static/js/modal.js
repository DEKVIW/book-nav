document.addEventListener("DOMContentLoaded", function () {
  const editLinkModal = document.getElementById("editLinkModal");
  const editLinkBtn = document.getElementById("editLink");
  const closeModalBtn = document.getElementById("closeModal");
  const cancelEditBtn = document.getElementById("cancelEdit");
  const editLinkForm = document.getElementById("editLinkForm");
  const fetchInfoBtn = document.getElementById("fetchInfo");
  const editIconPreview = document.getElementById("editIconPreview");

  // 创建加载状态覆盖层
  const modalBody = editLinkModal.querySelector(".modal-body");
  const loadingOverlay = document.createElement("div");
  loadingOverlay.className = "form-loading-overlay";
  loadingOverlay.innerHTML = `
    <div class="form-loading-spinner"></div>
    <div class="form-loading-text">正在加载...</div>
  `;

  // 确保modalBody是相对定位的，以便覆盖层可以正确定位
  modalBody.style.position = "relative";
  modalBody.appendChild(loadingOverlay);

  /**
   * 显示或隐藏表单加载状态
   * @param {boolean} isLoading - 是否显示加载状态
   * @param {string} loadingText - 加载状态文本
   */
  function setFormLoading(isLoading, loadingText = "正在加载...") {
    if (isLoading) {
      loadingOverlay.querySelector(".form-loading-text").textContent =
        loadingText;
      loadingOverlay.classList.add("show");
    } else {
      loadingOverlay.classList.remove("show");
    }
  }

  /**
   * 显示toast通知
   * @param {string} message - 显示的消息
   * @param {string} type - 通知类型：success, error, warning, info
   */
  function showToast(message, type = "success") {
    // 检查是否已存在提示，如果有则移除
    const existingToast = document.querySelector(".copy-toast");
    if (existingToast) {
      document.body.removeChild(existingToast);
    }

    // 根据类型确定图标和颜色
    let icon, backgroundColor, borderColor;
    switch (type) {
      case "error":
        icon = "bi-exclamation-circle-fill";
        backgroundColor = "linear-gradient(145deg, #f44336, #e53935)";
        borderColor = "#c62828";
        break;
      case "warning":
        icon = "bi-exclamation-triangle-fill";
        backgroundColor = "linear-gradient(145deg, #ff9800, #f57c00)";
        borderColor = "#e65100";
        break;
      case "info":
        icon = "bi-info-circle-fill";
        backgroundColor = "linear-gradient(145deg, #2196f3, #1e88e5)";
        borderColor = "#0d47a1";
        break;
      case "success":
      default:
        icon = "bi-check-circle-fill";
        backgroundColor =
          "var(--primary-gradient, linear-gradient(135deg, #7049f0, #aa26ff))";
        borderColor = "rgba(112, 73, 240, 0.7)";
        break;
    }

    // 创建新的提示元素
    const toast = document.createElement("div");
    toast.className = "copy-toast";
    toast.style.background = backgroundColor;
    toast.style.borderLeft = `4px solid ${borderColor}`;
    toast.innerHTML = `
      <i class="bi ${icon}"></i>
      <span>${message}</span>
    `;

    // 添加到页面
    document.body.appendChild(toast);

    // 延迟一小段时间后显示，以便有渐入效果
    setTimeout(() => {
      toast.classList.add("show");
    }, 10);

    // 2.5秒后自动消失
    setTimeout(() => {
      toast.classList.remove("show");

      // 动画结束后从DOM中移除
      setTimeout(() => {
        if (toast.parentNode) {
          document.body.removeChild(toast);
        }
      }, 300); // 等待过渡动画完成
    }, 2500);
  }

  // 修改链接按钮点击事件
  editLinkBtn.addEventListener("click", function () {
    if (window.currentCard) {
      const cardId = window.currentCard.href.split("/").pop();
      const cardTitle = window.currentCard
        .querySelector(".site-title")
        .textContent.trim();
      const cardDesc = window.currentCard
        .querySelector(".site-description")
        .textContent.trim();
      const cardIcon = window.currentCard.querySelector(".site-icon img");

      // 获取排序权重值 - 优先使用data-sort-order，不存在则使用data-sort
      let sortOrder = window.currentCard.getAttribute("data-sort-order");
      if (!sortOrder) {
        sortOrder = window.currentCard.getAttribute("data-sort");
      }

      // 填充表单
      document.getElementById("editLinkId").value = cardId;
      document.getElementById("editTitle").value = cardTitle;
      document.getElementById("editUrl").value = ""; // 获取URL需要额外请求
      document.getElementById("editDescription").value = cardDesc;

      // 如果能从DOM中获取权重值，则直接设置
      if (sortOrder) {
        document.getElementById("editWeight").value = sortOrder;
      }

      if (cardIcon) {
        document.getElementById("editIcon").value = cardIcon.src;
        // 更新图标预览
        editIconPreview.src = cardIcon.src;
        editIconPreview.style.display = "block";
      } else {
        document.getElementById("editIcon").value = "";
        editIconPreview.style.display = "none";
      }

      // 显示对话框
      editLinkModal.style.display = "flex";

      // 显示加载状态
      setFormLoading(true, "正在获取网站信息...");

      // 从服务器获取完整信息
      fetch(`/site/${cardId}/info`)
        .then((response) => response.json())
        .then((data) => {
          if (data.success) {
            document.getElementById("editUrl").value = data.website.url;
            // 更新描述，使用服务器返回的完整描述
            if (data.website.description) {
              document.getElementById("editDescription").value =
                data.website.description;
            }
            // 设置私有/公开状态
            if (data.website.is_private) {
              document.getElementById("editPrivate").checked = true;
            } else {
              document.getElementById("editPublic").checked = true;
            }

            // 只有在DOM中没有获取到权重值时，才从服务器数据中设置
            if (!sortOrder && data.website.sort_order !== undefined) {
              document.getElementById("editWeight").value =
                data.website.sort_order;
            }

            // 设置当前分类
            if (data.website.category && data.website.category.id) {
              const categorySelect = document.getElementById("editCategory");
              if (categorySelect) {
                categorySelect.value = data.website.category.id;
              }
            }
          }
        })
        .catch((error) => {
          console.error("获取网站信息出错:", error);
          showToast("获取网站信息失败，请手动填写", "error");
        })
        .finally(() => {
          // 隐藏加载状态
          setFormLoading(false);
        });
    }
  });

  // 监听图标输入框变化，更新预览
  document.getElementById("editIcon").addEventListener("input", function () {
    const iconUrl = this.value.trim();
    if (iconUrl) {
      editIconPreview.src = iconUrl;
      editIconPreview.setAttribute("referrerpolicy", "no-referrer");
      editIconPreview.style.display = "block";

      // 处理加载错误
      editIconPreview.onerror = function () {
        this.style.display = "none";
      };
    } else {
      editIconPreview.style.display = "none";
    }
  });

  // 关闭对话框
  closeModalBtn.addEventListener("click", function () {
    editLinkModal.style.display = "none";
  });

  cancelEditBtn.addEventListener("click", function () {
    editLinkModal.style.display = "none";
  });

  // 点击遮罩层关闭对话框
  editLinkModal.addEventListener("click", function (e) {
    if (e.target === this) {
      this.style.display = "none";
    }
  });

  // 处理表单提交
  editLinkForm.addEventListener("submit", async function (e) {
    e.preventDefault();

    // 检查是否支持详细进度显示
    const supportsDetailedProgress = isDetailedProgressSupported();

    const siteId = document.getElementById("editLinkId").value;
    const title = document.getElementById("editTitle").value;
    const url = document.getElementById("editUrl").value;
    const icon = document.getElementById("editIcon").value;
    const description = document.getElementById("editDescription").value;
    const categoryId = document.getElementById("editCategory").value;
    const sortOrder =
      parseInt(document.getElementById("editWeight").value) || 0;

    // 验证分类选择
    if (!categoryId) {
      showToast("请选择分类", "info");
      return;
    }

    try {
      // 显示加载状态
      setFormLoading(true, "正在保存修改...");

      if (supportsDetailedProgress) {
        updateProgress("正在验证数据...", 10);
      }

      // 检查URL是否已存在（排除当前编辑的链接）
      if (supportsDetailedProgress) {
        updateProgress("正在检查URL是否存在...", 30);
      }

      const checkResponse = await fetch(
        `/api/check_url_exists?url=${encodeURIComponent(
          url
        )}&exclude_id=${siteId}`
      );
      const checkResult = await checkResponse.json();

      if (checkResult.exists) {
        // 隐藏加载状态，因为将显示确认对话框
        setFormLoading(false);

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

        // 用户选择继续添加，重新显示加载状态
        setFormLoading(true, "正在保存修改...");
      }

      // 发送修改请求到服务器
      if (supportsDetailedProgress) {
        updateProgress("正在发送数据到服务器...", 60);
      }

      const response = await fetch(`/api/website/update/${siteId}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')
            .content,
        },
        body: JSON.stringify({
          title: title,
          url: url,
          icon: icon,
          description: description,
          is_private: document.getElementById("editPrivate").checked ? 1 : 0,
          category_id: parseInt(categoryId),
          sort_order: sortOrder,
        }),
      });

      if (supportsDetailedProgress) {
        updateProgress("正在处理服务器响应...", 80);
      }

      const data = await response.json();
      if (data.success) {
        // 判断分类是否改变，如果改变则刷新页面
        const originalCategoryId =
          window.currentCard.closest(".card-container")?.dataset.categoryId;
        if (originalCategoryId && originalCategoryId !== categoryId) {
          // 分类已更改，刷新页面
          alert("链接分类已更改，页面将重新加载");
          window.location.reload();
          return;
        }

        // 分类未改变，更新卡片显示
        if (window.currentCard) {
          const titleEl = window.currentCard.querySelector(".site-title");
          const descEl = window.currentCard.querySelector(".site-description");
          const iconImg = window.currentCard.querySelector(".site-icon img");
          const iconContainer = window.currentCard.querySelector(".site-icon");

          if (titleEl) titleEl.textContent = title.trim();
          if (descEl) descEl.textContent = description.trim();

          // 更新图标
          if (icon) {
            if (iconImg) {
              iconImg.src = icon;
            } else {
              // 如果之前没有图标，创建一个
              iconContainer.innerHTML = `<img src="${icon}" alt="${title}" referrerpolicy="no-referrer">`;
            }
          } else if (iconImg) {
            // 如果清除了图标，显示默认图标（使用网站标题首字母）
            const firstLetter = title.trim().charAt(0).toUpperCase();
            iconContainer.innerHTML = `<div class="default-site-icon">${firstLetter}</div>`;
          }

          // 更新卡片的data-description属性
          window.currentCard.setAttribute(
            "data-description",
            description.trim()
          );
        }

        if (supportsDetailedProgress) {
          updateProgress("修改成功！", 100);
        }

        showToast("网站信息修改成功!");

        // 关闭模态框而不刷新页面
        editLinkModal.style.display = "none";
      } else {
        showToast("修改失败: " + data.message, "error");
      }
    } catch (error) {
      console.error("修改链接出错:", error);
      showToast("修改链接时发生错误，请重试", "error");
    } finally {
      // 隐藏加载状态
      setFormLoading(false);
    }
  });

  // 自动获取网站信息
  fetchInfoBtn.addEventListener("click", function () {
    const urlInput = document.getElementById("editUrl");
    const titleInput = document.getElementById("editTitle");
    const descInput = document.getElementById("editDescription");
    const iconInput = document.getElementById("editIcon");
    const url = urlInput.value.trim();

    if (!url) {
      showToast("请先输入网站链接地址", "info");
      return;
    }

    // 显示全局加载状态
    setFormLoading(true, "正在获取网站信息...");

    // 显示按钮加载状态
    this.classList.add("loading");

    // 检查是否支持流式响应
    const supportsStreaming =
      "ReadableStream" in window &&
      "getReader" in window.ReadableStream.prototype;

    if (supportsStreaming) {
      // 使用新的流式API获取网站信息
      fetchWithProgress(url, titleInput, descInput, iconInput, this);
    } else {
      // 使用传统方式获取网站信息
      fetchWebsiteInfoTraditional(url, titleInput, descInput, iconInput, this);
    }
  });

  // 使用流式响应获取网站信息（带进度）
  function fetchWithProgress(url, titleInput, descInput, iconInput, button) {
    // 创建AbortController用于可能的请求取消
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000); // 30秒超时

    fetch(
      `/api/fetch_website_info_with_progress?url=${encodeURIComponent(url)}`,
      {
        signal: controller.signal,
        headers: {
          Accept: "text/event-stream",
        },
      }
    )
      .then((response) => {
        // 检查响应是否成功
        if (!response.ok) {
          throw new Error(`HTTP错误: ${response.status}`);
        }

        // 获取响应体的reader
        const reader = response.body.getReader();
        let decoder = new TextDecoder();
        let buffer = "";

        // 递归读取流数据
        function readChunk() {
          return reader.read().then(({ value, done }) => {
            if (done) {
              // 流读取完毕，处理可能的剩余数据
              if (buffer) {
                try {
                  const lastEvent = JSON.parse(buffer);
                  processProgressEvent(
                    lastEvent,
                    titleInput,
                    descInput,
                    iconInput
                  );
                } catch (e) {
                  console.error("解析最终数据出错:", e);
                }
              }

              // 隐藏加载状态
              button.classList.remove("loading");
              setFormLoading(false);
              clearTimeout(timeoutId);
              return;
            }

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
                processProgressEvent(event, titleInput, descInput, iconInput);
              } catch (e) {
                console.error("解析进度数据出错:", e, line);
              }
            }

            // 继续读取下一块数据
            return readChunk();
          });
        }

        return readChunk();
      })
      .catch((error) => {
        console.error("流式获取网站信息出错:", error);

        if (error.name === "AbortError") {
          showToast("获取网站信息超时，请手动填写", "warning");
        } else {
          showToast("获取网站信息失败，请手动填写", "warning");
        }

        // 隐藏加载状态
        button.classList.remove("loading");
        setFormLoading(false);
        clearTimeout(timeoutId);
      });
  }

  // 处理进度事件
  function processProgressEvent(event, titleInput, descInput, iconInput) {
    // 更新进度显示
    updateProgress(event.message, event.progress);

    // 如果是最终结果，填充表单
    if (event.stage === "complete" && event.success) {
      // 更新标题
      if (event.title) {
        titleInput.value = event.title;
      }

      // 更新描述
      if (event.description) {
        descInput.value = event.description;
      }

      // 设置图标URL
      if (event.icon_url) {
        iconInput.value = event.icon_url;
      }

      showToast("网站信息获取成功!");
    } else if (event.stage === "error") {
      // 显示错误信息
      showToast("获取网站信息失败: " + event.message, "error");
    }
  }

  // 传统方式获取网站信息
  function fetchWebsiteInfoTraditional(
    url,
    titleInput,
    descInput,
    iconInput,
    button
  ) {
    const supportsDetailedProgress = isDetailedProgressSupported();

    if (supportsDetailedProgress) {
      updateProgress("正在准备获取网站信息...", 10);
    }

    // 请求网站信息
    fetch(`/api/fetch_website_info?url=${encodeURIComponent(url)}`)
      .then((response) => {
        if (supportsDetailedProgress) {
          updateProgress("正在获取网站基本信息...", 40);
        }
        return response.json();
      })
      .then(async (data) => {
        // 如果获取失败或信息不完整，尝试使用AI填充
        const hasTitle = data.title && data.title.trim() !== "";
        const hasDesc = data.description && data.description.trim() !== "";
        
        if ((!data.success || !hasTitle || !hasDesc) && supportsDetailedProgress) {
          updateProgress("正在使用AI生成网站信息...", 50);
          
          try {
            const aiResponse = await fetch(
              `/api/fetch_website_info?url=${encodeURIComponent(url)}&use_ai_fallback=true`
            );
            const aiData = await aiResponse.json();
            
            // 如果AI成功生成，使用AI结果
            if (aiData.success) {
              if (!hasTitle && aiData.title) {
                data.title = aiData.title;
                data.ai_generated_title = true;
              }
              if (!hasDesc && aiData.description) {
                data.description = aiData.description;
                data.ai_generated_description = true;
              }
            }
          } catch (error) {
            console.warn("AI生成网站信息失败:", error);
          }
        }
        
        if (data.success) {
          // 直接更新标题和描述，无论是否为空
          if (data.title) {
            titleInput.value = data.title;
          }

          if (data.description) {
            descInput.value = data.description;
          }

          if (supportsDetailedProgress) {
            updateProgress("正在获取网站图标...", 60);
          }

          // 解析域名获取图标
          try {
            let domain = url;
            if (url.startsWith("http")) {
              const urlObj = new URL(url);
              domain = urlObj.hostname;
            } else if (url.includes("/")) {
              domain = url.split("/")[0];
            }

            // 使用API获取图标
            const requestOptions = {
              method: "GET",
              redirect: "follow",
            };

            fetch(
              `/api/get_website_icon?url=${encodeURIComponent(url)}`,
              requestOptions
            )
              .then((response) => {
                if (supportsDetailedProgress) {
                  updateProgress("正在处理图标数据...", 80);
                }
                return response.json();
              })
              .then((iconData) => {
                if (iconData.success && iconData.icon_url) {
                  iconInput.value = iconData.icon_url;
                } else if (iconData.fallback_url) {
                  // 如果API获取失败但有备用服务
                  iconInput.value = iconData.fallback_url;
                } else {
                  // 如果API获取失败，使用备用服务
                  iconInput.value = `https://favicon.cccyun.cc/${domain}`;
                }

                if (supportsDetailedProgress) {
                  updateProgress("网站信息获取完成!", 100);
                }
              })
              .catch(() => {
                // 如果请求出错，使用备用服务
                iconInput.value = `https://favicon.cccyun.cc/${domain}`;

                if (supportsDetailedProgress) {
                  updateProgress("图标获取失败，使用备用服务", 95);
                }
              });
          } catch (error) {
            console.error("解析域名出错:", error);
            if (supportsDetailedProgress) {
              updateProgress("解析域名出错，使用备用服务", 90);
            }
          }

          showToast("网站信息获取成功!");
        } else {
          showToast(
            "获取网站信息失败: " + (data.message || "未知错误"),
            "error"
          );
        }
      })
      .catch((error) => {
        console.error("获取网站信息出错:", error);
        showToast("获取网站信息失败，请手动填写", "warning");
      })
      .finally(() => {
        // 移除按钮加载状态
        button.classList.remove("loading");
        // 移除全局加载状态
        setFormLoading(false);
      });
  }

  // 检查是否支持详细进度展示
  function isDetailedProgressSupported() {
    // 在这里添加逻辑来确定是否应该显示详细进度
    // 例如，根据浏览器或设备类型，或者用户偏好设置
    return true; // 默认支持
  }

  // 更新进度显示的函数
  function updateProgress(message, percent) {
    // 如果存在进度元素则更新
    const progressText = document.querySelector(".form-loading-text");
    if (progressText) {
      progressText.textContent = message;
    }

    // 如果有进度条元素，更新它的宽度
    const progressBar = document.querySelector(".progress-bar");
    if (progressBar) {
      progressBar.style.width = `${percent}%`;
      progressBar.setAttribute("aria-valuenow", percent);
    }
  }

  // 翻译描述文本为中文
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
    translateBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    
    try {
      const response = await fetch("/api/translate_description", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "",
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

  // 绑定编辑表单的翻译按钮
  const editTranslateBtn = document.getElementById("editTranslateBtn");
  if (editTranslateBtn) {
    editTranslateBtn.addEventListener("click", function () {
      translateDescription("editDescription", "editTranslateBtn");
    });
  }
});
