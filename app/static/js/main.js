function collectTooltipElements(root) {
  if (!root) {
    return [];
  }

  if (root.matches && root.matches('[data-bs-toggle="tooltip"]')) {
    return [root].concat(
      Array.from(root.querySelectorAll('[data-bs-toggle="tooltip"]'))
    );
  }

  return Array.from(root.querySelectorAll('[data-bs-toggle="tooltip"]'));
}

function buildTooltipOptions(element) {
  const isSiteCard = element.classList.contains("site-card");

  return {
    trigger: element.getAttribute("data-bs-trigger") || "hover",
    placement: element.getAttribute("data-bs-placement") || "bottom",
    fallbackPlacements: ["top"],
    container: "body",
    boundary: document.body,
    html: element.getAttribute("data-bs-html") === "true",
    animation: !isSiteCard,
  };
}

window.initBootstrapTooltips = function (root = document) {
  if (typeof bootstrap === "undefined" || !bootstrap.Tooltip) {
    return [];
  }

  return collectTooltipElements(root).map(function (tooltipTriggerEl) {
    return bootstrap.Tooltip.getOrCreateInstance(
      tooltipTriggerEl,
      buildTooltipOptions(tooltipTriggerEl)
    );
  });
};

window.hideBootstrapTooltips = function (root = document, removeOrphans = false) {
  if (typeof bootstrap !== "undefined" && bootstrap.Tooltip) {
    collectTooltipElements(root).forEach(function (element) {
      const tooltipInstance = bootstrap.Tooltip.getInstance(element);
      if (tooltipInstance) {
        tooltipInstance.hide();
      }
    });
  }

  if (removeOrphans) {
    document.body.querySelectorAll(".tooltip").forEach(function (tooltip) {
      tooltip.remove();
    });
  }
};

window.disposeBootstrapTooltips = function (root = document, removeOrphans = false) {
  if (typeof bootstrap !== "undefined" && bootstrap.Tooltip) {
    collectTooltipElements(root).forEach(function (element) {
      const tooltipInstance = bootstrap.Tooltip.getInstance(element);
      if (tooltipInstance) {
        tooltipInstance.dispose();
      }
    });
  }

  if (removeOrphans) {
    document.body.querySelectorAll(".tooltip").forEach(function (tooltip) {
      tooltip.remove();
    });
  }
};

// 等待文档加载完成
document.addEventListener("DOMContentLoaded", function () {
  window.initBootstrapTooltips(document);

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      window.hideBootstrapTooltips(document, true);
    }
  });

  window.addEventListener("blur", function () {
    window.hideBootstrapTooltips(document, true);
  });

  window.addEventListener("pageshow", function () {
    window.hideBootstrapTooltips(document, true);
  });

  // 添加导航栏滚动效果
  var navbar = document.querySelector(".navbar");
  if (navbar) {
    window.addEventListener("scroll", function () {
      if (window.scrollY > 50) {
        navbar.classList.add("navbar-scrolled", "shadow-sm");
      } else {
        navbar.classList.remove("navbar-scrolled", "shadow-sm");
      }
    });
  }

  // 为所有卡片添加鼠标悬停动画效果
  var cards = document.querySelectorAll(".card:not(.no-hover)");
  cards.forEach(function (card) {
    card.classList.add("animated-hover");
  });

  // 添加返回顶部按钮功能
  var backToTopBtn = document.getElementById("back-to-top");
  if (backToTopBtn) {
    window.addEventListener("scroll", function () {
      if (window.scrollY > 300) {
        backToTopBtn.classList.add("show");
      } else {
        backToTopBtn.classList.remove("show");
      }
    });

    backToTopBtn.addEventListener("click", function (e) {
      e.preventDefault();
      window.scrollTo({
        top: 0,
        behavior: "smooth",
      });
    });
  }

  // 为搜索框添加焦点效果
  var searchInput = document.querySelector('input[type="search"]');
  if (searchInput) {
    searchInput.addEventListener("focus", function () {
      this.parentElement.classList.add("search-focused");
    });

    searchInput.addEventListener("blur", function () {
      this.parentElement.classList.remove("search-focused");
    });
  }

  // 添加图片懒加载
  var lazyImages = [].slice.call(document.querySelectorAll("img.lazy"));
  if ("IntersectionObserver" in window) {
    let lazyImageObserver = new IntersectionObserver(function (
      entries,
      observer
    ) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          let lazyImage = entry.target;
          lazyImage.src = lazyImage.dataset.src;
          lazyImage.classList.remove("lazy");
          lazyImageObserver.unobserve(lazyImage);
        }
      });
    });

    lazyImages.forEach(function (lazyImage) {
      lazyImageObserver.observe(lazyImage);
    });
  }

  // 数字增长动画
  function animateValue(obj, start, end, duration) {
    if (start === end) return;
    var range = end - start;
    var current = start;
    var increment = end > start ? 1 : -1;
    var stepTime = Math.abs(Math.floor(duration / range));
    var timer = setInterval(function () {
      current += increment;
      obj.textContent = current;
      if (current == end) {
        clearInterval(timer);
      }
    }, stepTime);
  }

  // 为统计数字添加动画
  var statsNumbers = document.querySelectorAll(".stat-number");
  if (statsNumbers.length > 0) {
    statsNumbers.forEach(function (numberElement) {
      var finalValue = parseInt(numberElement.getAttribute("data-value"));
      animateValue(numberElement, 0, finalValue, 1000);
    });
  }

  // 用户头像下拉菜单点击切换
  var userDropdownToggle = document.querySelector(".user-dropdown-toggle");
  var userDropdownMenu = document.querySelector(".user-dropdown-menu");
  if (userDropdownToggle && userDropdownMenu) {
    userDropdownToggle.addEventListener("click", function (e) {
      e.stopPropagation();
      userDropdownMenu.classList.toggle("show");
    });
    document.addEventListener("click", function () {
      userDropdownMenu.classList.remove("show");
    });
    userDropdownMenu.addEventListener("click", function (e) {
      e.stopPropagation();
    });
  }
});
