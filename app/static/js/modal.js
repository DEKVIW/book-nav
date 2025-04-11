document.addEventListener("DOMContentLoaded", function () {
  const editLinkModal = document.getElementById("editLinkModal");
  const editLinkBtn = document.getElementById("editLink");
  const closeModalBtn = document.getElementById("closeModal");
  const cancelEditBtn = document.getElementById("cancelEdit");
  const editLinkForm = document.getElementById("editLinkForm");
  const fetchInfoBtn = document.getElementById("fetchInfo");

  // 修改链接按钮点击事件
  editLinkBtn.addEventListener("click", function () {
    if (window.currentCard) {
      const cardId = window.currentCard.href.split("/").pop();
      const cardTitle =
        window.currentCard.querySelector(".site-title").textContent;
      const cardDesc =
        window.currentCard.querySelector(".site-description").textContent;
      const cardIcon = window.currentCard.querySelector(".site-icon img");

      // 填充表单
      document.getElementById("editLinkId").value = cardId;
      document.getElementById("editTitle").value = cardTitle;
      document.getElementById("editUrl").value = ""; // 获取URL需要额外请求
      document.getElementById("editDescription").value = cardDesc;

      if (cardIcon) {
        document.getElementById("editIcon").value = cardIcon.src;
      } else {
        document.getElementById("editIcon").value = "";
      }

      // 从服务器获取完整信息
      fetch(`/site/${cardId}/info`)
        .then((response) => response.json())
        .then((data) => {
          if (data.success) {
            document.getElementById("editUrl").value = data.website.url;
          }
        })
        .catch((error) => {
          console.error("获取网站信息出错:", error);
        });

      // 显示对话框
      editLinkModal.style.display = "flex";
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
  editLinkForm.addEventListener("submit", function (e) {
    e.preventDefault();

    const siteId = document.getElementById("editLinkId").value;
    const title = document.getElementById("editTitle").value;
    const url = document.getElementById("editUrl").value;
    const icon = document.getElementById("editIcon").value;
    const description = document.getElementById("editDescription").value;

    // 发送修改请求到服务器
    fetch(`/api/website/update/${siteId}`, {
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
      }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.success) {
          // 关闭对话框
          editLinkModal.style.display = "none";

          // 更新卡片显示
          if (window.currentCard) {
            const titleEl = window.currentCard.querySelector(".site-title");
            const descEl =
              window.currentCard.querySelector(".site-description");
            const iconImg = window.currentCard.querySelector(".site-icon img");
            const iconContainer =
              window.currentCard.querySelector(".site-icon");

            if (titleEl) titleEl.textContent = title;
            if (descEl) descEl.textContent = description;

            // 更新图标
            if (icon) {
              if (iconImg) {
                iconImg.src = icon;
              } else {
                // 如果之前没有图标，创建一个
                iconContainer.innerHTML = `<img src="${icon}" alt="${title}">`;
              }
            } else if (iconImg) {
              // 如果清除了图标，显示默认图标
              iconContainer.innerHTML =
                '<i class="bi bi-globe text-primary"></i>';
            }
          }

          alert("网站信息修改成功!");

          // 刷新页面以确保所有内容都是最新的
          setTimeout(() => {
            window.location.reload();
          }, 1000);
        } else {
          alert("修改失败: " + data.message);
        }
      })
      .catch((error) => {
        console.error("修改链接出错:", error);
        alert("修改链接时发生错误，请重试");
      });
  });

  // 自动获取网站信息
  fetchInfoBtn.addEventListener("click", function () {
    const urlInput = document.getElementById("editUrl");
    const titleInput = document.getElementById("editTitle");
    const descInput = document.getElementById("editDescription");
    const iconInput = document.getElementById("editIcon");
    const url = urlInput.value.trim();

    if (!url) {
      alert("请先输入网站链接地址");
      return;
    }

    // 显示加载状态
    this.classList.add("loading");

    // 请求网站信息
    fetch(`/api/fetch_website_info?url=${encodeURIComponent(url)}`)
      .then((response) => response.json())
      .then((data) => {
        if (data.success) {
          // 如果当前标题或描述为空，自动填充
          if (data.title && !titleInput.value.trim()) {
            titleInput.value = data.title;
          }

          if (data.description && !descInput.value.trim()) {
            descInput.value = data.description;
          }

          // 解析域名获取图标
          if (!iconInput.value.trim()) {
            try {
              let domain = url;
              if (url.startsWith("http")) {
                const urlObj = new URL(url);
                domain = urlObj.hostname;
              } else if (url.includes("/")) {
                domain = url.split("/")[0];
              }

              // 检查图标是否可用
              const testImg = new Image();
              testImg.onload = function () {
                if (testImg.width < 8 || testImg.height < 8) {
                  // 如果图标太小，使用备用图标（使用Google的favicon服务）
                  iconInput.value = `https://www.google.com/s2/favicons?domain=${domain}&sz=64`;
                } else {
                  iconInput.value = `https://favicon.cccyun.cc/${domain}`;
                }
              };
              testImg.onerror = function () {
                // 如果图标加载失败，使用备用图标
                iconInput.value = `https://www.google.com/s2/favicons?domain=${domain}&sz=64`;
              };
              testImg.src = `https://favicon.cccyun.cc/${domain}`;

              // 默认先填入，如果有错误会被上面的回调更新
              iconInput.value = `https://favicon.cccyun.cc/${domain}`;
            } catch (error) {
              console.error("解析域名出错:", error);
            }
          }

          alert("网站信息获取成功！");
        } else {
          alert("获取网站信息失败: " + (data.message || "未知错误"));
        }
      })
      .catch((error) => {
        console.error("获取网站信息出错:", error);
        alert("获取网站信息失败，请手动填写");
      })
      .finally(() => {
        // 移除加载状态
        this.classList.remove("loading");
      });
  });
});
