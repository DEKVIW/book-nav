document.addEventListener("DOMContentLoaded", function () {
  // 移除预加载类，恢复过渡效果
  setTimeout(function () {
    document.documentElement.classList.remove("sidebar-active-preload");
  }, 50);

  // 获取DOM元素
  const menuToggle = document.getElementById("menuToggle");
  const sidebar = document.getElementById("sidebar");
  const sidebarOverlay = document.getElementById("sidebarOverlay");
  const bodyElement = document.body;

  // 获取页面类型
  const pageType =
    document.documentElement.getAttribute("data-page-type") || "default";

  // 检查是否有保存的侧边栏状态
  const isMobile = window.innerWidth < 768;
  const storageKey =
    "sidebarActive" + (pageType !== "default" ? "-" + pageType : "");
  let savedSidebarState = localStorage.getItem(storageKey);

  // 如果是分类页面且没有保存过状态，默认关闭侧边栏
  if (pageType === "category" && savedSidebarState === null) {
    savedSidebarState = "false";
  }

  // 优先使用保存的状态，如果没有则默认关闭
  if (savedSidebarState !== null) {
    if (savedSidebarState === "true") {
      bodyElement.classList.add("sidebar-active");
    } else {
      bodyElement.classList.remove("sidebar-active");
    }
  } else {
    // 默认关闭侧边栏
    bodyElement.classList.remove("sidebar-active");
  }

  // 菜单切换功能
  menuToggle.addEventListener("click", function () {
    bodyElement.classList.toggle("sidebar-active");
    // 保存当前状态到localStorage，根据页面类型使用不同的键
    localStorage.setItem(
      storageKey,
      bodyElement.classList.contains("sidebar-active")
    );
  });

  // 点击遮罩层关闭侧边栏
  sidebarOverlay.addEventListener("click", function () {
    bodyElement.classList.remove("sidebar-active");
    // 保存当前状态到localStorage
    localStorage.setItem(storageKey, "false");
  });

  // 点击链接平滑滚动并关闭侧边栏
  document.querySelectorAll(".sidebar-menu-link").forEach((link) => {
    link.addEventListener("click", function (e) {
      const href = this.getAttribute("href");

      if (href.startsWith("#")) {
        e.preventDefault();
        const targetElement = document.getElementById(href.substring(1));

        if (targetElement) {
          window.scrollTo({
            top: targetElement.offsetTop - 80,
            behavior: "smooth",
          });

          // 在移动设备上关闭侧边栏
          if (window.innerWidth < 768) {
            bodyElement.classList.remove("sidebar-active");
            // 保存当前状态到localStorage
            localStorage.setItem(storageKey, "false");
          }

          // 如果在搜索结果页面，先清除搜索
          const searchResults = document.getElementById("searchResults");
          if (searchResults && searchResults.style.display !== "none") {
            window.dispatchEvent(new Event("clearSearch"));
          }
        }
      }
    });
  });

  // 监听窗口大小变化
  window.addEventListener("resize", function () {
    const isMobile = window.innerWidth < 768;
    const currentState = bodyElement.classList.contains("sidebar-active");
    const savedState = localStorage.getItem(storageKey);

    // 保持用户当前状态，不自动调整
    // 如果用户没有明确设置过状态，保持关闭
    if (savedState === null) {
      bodyElement.classList.remove("sidebar-active");
    }
  });
});
