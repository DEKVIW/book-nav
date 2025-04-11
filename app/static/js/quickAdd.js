document.addEventListener("paste", async function (e) {
  // 只有管理员才能使用快速添加功能
  if (!document.body.classList.contains("user-admin")) {
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
  setQuickAddLoading(true);

  try {
    // 获取网站信息和图标
    const [websiteInfo, iconUrl] = await Promise.all([
      fetch(
        `/api/fetch_website_info?url=${encodeURIComponent(pastedData)}`
      ).then((r) => r.json()),
      getFaviconUrl(pastedData),
    ]);

    // 填充表单
    document.getElementById("quickAddTitle").value = websiteInfo.title || "";
    document.getElementById("quickAddUrl").value = pastedData;
    document.getElementById("quickAddDescription").value =
      websiteInfo.description || "";

    // 设置图标
    if (iconUrl) {
      const iconInput = document.getElementById("quickAddIcon");
      const iconPreview = document.getElementById("quickAddIconPreview");
      iconInput.value = iconUrl;
      iconPreview.src = iconUrl;
      iconPreview.style.display = "block";
    }
  } catch (error) {
    console.error("获取网站信息失败:", error);
    // 如果获取失败，至少填充URL
    document.getElementById("quickAddUrl").value = pastedData;
  } finally {
    setQuickAddLoading(false);
  }
});

// 获取网站图标的函数
function getFaviconUrl(url) {
  try {
    const urlObj = new URL(url);
    const domain = urlObj.hostname;
    return `https://favicon.cccyun.cc/${domain}`;
  } catch (error) {
    console.error("解析域名失败:", error);
    return null;
  }
}

function isValidUrl(string) {
  try {
    const url = new URL(string);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch (_) {
    return false;
  }
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
}

function setQuickAddLoading(isLoading) {
  const submitBtn = document.querySelector("#quickAddModal .btn-primary");
  if (isLoading) {
    submitBtn.disabled = true;
    submitBtn.innerHTML =
      '<span class="spinner-border spinner-border-sm me-1"></span>加载中...';
  } else {
    submitBtn.disabled = false;
    submitBtn.innerHTML = "添加";
  }
}

async function submitQuickAdd() {
  const categoryId = document.getElementById("quickAddCategory").value;
  if (!categoryId) {
    alert("请选择分类");
    return;
  }

  const submitBtn = document.querySelector("#quickAddModal .btn-primary");
  submitBtn.disabled = true;
  submitBtn.innerHTML =
    '<span class="spinner-border spinner-border-sm me-1"></span>提交中...';

  const data = {
    title: document.getElementById("quickAddTitle").value.trim(),
    url: document.getElementById("quickAddUrl").value.trim(),
    description: document.getElementById("quickAddDescription").value.trim(),
    icon: document.getElementById("quickAddIcon").value.trim(),
    category_id: parseInt(categoryId),
  };

  try {
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
      closeQuickAddModal();
      // 刷新页面以显示新添加的链接
      window.location.reload();
    } else {
      alert(result.message || "添加失败");
    }
  } catch (error) {
    console.error("提交失败:", error);
    alert("提交失败，请重试");
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = "添加";
  }
}
