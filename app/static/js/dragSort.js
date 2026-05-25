let draggedCard = null;

function preventNativeDrag(event) {
  event.preventDefault();
}

function disableNativeCardDrag(card) {
  card.setAttribute("draggable", "false");
  card.addEventListener("dragstart", preventNativeDrag);

  card.querySelectorAll("img").forEach((img) => {
    img.setAttribute("draggable", "false");
    img.addEventListener("dragstart", preventNativeDrag);
  });
}

function enableDragSort(container) {
  if (!container) {
    return;
  }

  const cards = container.querySelectorAll(".site-card.draggable");

  cards.forEach((card) => {
    if (card.dataset.dragSortBound === "true") {
      return;
    }

    card.dataset.dragSortBound = "true";
    disableNativeCardDrag(card);
    card.addEventListener("mousedown", handleCardMouseDown);
    card.addEventListener("click", suppressClickAfterRecentDrag);

    const dragHandle = card.querySelector(".drag-handle");
    if (dragHandle) {
      dragHandle.addEventListener("mousedown", handleHandleMouseDown);
      dragHandle.addEventListener("click", suppressHandleInteraction);
      dragHandle.addEventListener("touchstart", handleHandleTouchStart, {
        passive: false,
      });
      dragHandle.addEventListener("touchend", suppressHandleInteraction, {
        passive: false,
      });
    }

    function handleCardMouseDown(event) {
      if (event.button !== 0 || event.target.closest(".drag-handle")) {
        return;
      }

      const initialX = event.clientX;
      const initialY = event.clientY;

      const handleMouseMove = (moveEvent) => {
        const deltaX = Math.abs(moveEvent.clientX - initialX);
        const deltaY = Math.abs(moveEvent.clientY - initialY);

        if (deltaX < 6 && deltaY < 6) {
          return;
        }

        cleanupMouseIntent();
        moveEvent.preventDefault();
        startDragging(card, container, moveEvent.clientX, moveEvent.clientY);
      };

      const handleMouseUp = () => {
        cleanupMouseIntent();
      };

      const cancelMouseIntent = () => {
        cleanupMouseIntent();
      };

      function cleanupMouseIntent() {
        document.removeEventListener("mousemove", handleMouseMove);
        document.removeEventListener("mouseup", handleMouseUp);
        document.removeEventListener("mouseleave", cancelMouseIntent);
      }

      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
      document.addEventListener("mouseleave", cancelMouseIntent);
    }

    function handleHandleMouseDown(event) {
      if (event.button !== 0) {
        return;
      }

      event.stopPropagation();
      event.preventDefault();
      startDragging(card, container, event.clientX, event.clientY);
    }

    function handleHandleTouchStart(event) {
      event.stopPropagation();
      event.preventDefault();

      const touch = event.touches[0];
      startDragging(card, container, touch.clientX, touch.clientY);
    }

    function suppressHandleInteraction(event) {
      event.stopPropagation();
      event.preventDefault();
    }

    function suppressClickAfterRecentDrag(event) {
      const justDraggedAt = parseInt(card.dataset.justDraggedAt || "0", 10);
      if (!justDraggedAt) {
        return;
      }

      if (Date.now() - justDraggedAt < 250) {
        event.preventDefault();
        event.stopPropagation();
      }

      card.dataset.justDraggedAt = "";
    }
  });
}

function startDragging(card, container, initialX, initialY) {
  if (draggedCard) {
    return;
  }

  draggedCard = card;
  draggedCard.classList.add("dragging");

  const rect = card.getBoundingClientRect();

  draggedCard.style.position = "fixed";
  draggedCard.style.zIndex = "1000";
  draggedCard.style.left = rect.left + "px";
  draggedCard.style.top = rect.top + "px";
  draggedCard.style.width = rect.width + "px";
  draggedCard.style.height = rect.height + "px";

  draggedCard.originalHref = draggedCard.getAttribute("href");
  draggedCard.removeAttribute("href");

  moveAt(initialX, initialY);

  document.addEventListener("mousemove", onMouseMove);
  document.addEventListener("touchmove", onTouchMove, { passive: false });
  document.addEventListener("mouseup", onMouseUp);
  document.addEventListener("touchend", onMouseUp);

  function onMouseMove(event) {
    moveAt(event.clientX, event.clientY);
  }

  function onTouchMove(event) {
    event.preventDefault();
    const touch = event.touches[0];
    moveAt(touch.clientX, touch.clientY);
  }

  function moveAt(x, y) {
    if (!draggedCard) {
      return;
    }

    draggedCard.style.left = x - draggedCard.offsetWidth / 2 + "px";
    draggedCard.style.top = y - draggedCard.offsetHeight / 2 + "px";

    const elemBelow = getElementBelow(x, y);

    if (elemBelow && elemBelow !== draggedCard) {
      const targetRect = elemBelow.getBoundingClientRect();
      const middleY = targetRect.y + targetRect.height / 2;

      if (y < middleY && elemBelow.previousElementSibling !== draggedCard) {
        elemBelow.parentNode.insertBefore(draggedCard, elemBelow);
      } else if (
        y >= middleY &&
        elemBelow.nextElementSibling !== draggedCard
      ) {
        elemBelow.parentNode.insertBefore(
          draggedCard,
          elemBelow.nextElementSibling
        );
      }
    }
  }

  function onMouseUp() {
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("touchmove", onTouchMove);
    document.removeEventListener("mouseup", onMouseUp);
    document.removeEventListener("touchend", onMouseUp);

    if (!draggedCard) {
      return;
    }

    draggedCard.classList.remove("dragging");
    draggedCard.style.position = "";
    draggedCard.style.zIndex = "";
    draggedCard.style.left = "";
    draggedCard.style.top = "";
    draggedCard.style.width = "";
    draggedCard.style.height = "";

    if (draggedCard.originalHref) {
      draggedCard.setAttribute("href", draggedCard.originalHref);
      draggedCard.originalHref = null;
    }

    draggedCard.dataset.justDraggedAt = String(Date.now());
    updateSortOrder(container);
    draggedCard = null;
  }
}

function getElementBelow(x, y) {
  if (!draggedCard) {
    return null;
  }

  const originalDisplay = draggedCard.style.display;
  draggedCard.style.display = "none";

  let elemBelow = document.elementFromPoint(x, y);

  draggedCard.style.display = originalDisplay;

  if (elemBelow) {
    return elemBelow.closest(".site-card");
  }

  return null;
}

function updateSortOrder(container) {
  const cards = container.querySelectorAll(".site-card");
  if (!cards.length) {
    return;
  }

  const categoryId = container.dataset.categoryId;
  const items = [];

  fetch(`/api/category/${categoryId}/count`)
    .then((response) => response.json())
    .then((data) => {
      const totalWebsites = data.total_count || cards.length;

      cards.forEach((card, index) => {
        const websiteId = parseInt(card.dataset.id, 10);
        if (isNaN(websiteId)) {
          return;
        }

        const newSortOrder = totalWebsites - index;
        card.dataset.sort = newSortOrder;
        card.setAttribute("data-sort-order", newSortOrder);

        items.push({
          id: websiteId,
          sort_order: newSortOrder,
        });
      });

      fetch("/api/website/update_order", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          category_id: categoryId,
          items: items,
        }),
        credentials: "same-origin",
      }).catch(() => {});
    })
    .catch((error) => {
      console.error("failed to load website count before updating order", error);
      if (confirm("排序需要刷新页面以确保顺序准确，是否立即刷新？")) {
        window.location.reload();
      }
    });
}

window.enableDragSort = enableDragSort;

document.addEventListener("DOMContentLoaded", function () {
  const cardContainers = document.querySelectorAll(".card-container.draggable");

  cardContainers.forEach((container) => {
    enableDragSort(container);
  });
});
