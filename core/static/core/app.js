(() => {
  "use strict";
  const menu = document.querySelector("#menu");
  const sidebar = document.querySelector(".sidebar");
  menu?.addEventListener("click", () => sidebar?.classList.toggle("open"));
  document.addEventListener("click", (event) => {
    if (
      sidebar?.classList.contains("open") &&
      !sidebar.contains(event.target) &&
      event.target !== menu
    ) {
      sidebar.classList.remove("open");
    }
  });
  document.querySelectorAll("[data-set-status]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("select.status").forEach((select) => {
        select.value = button.dataset.setStatus;
      });
    });
  });
})();
