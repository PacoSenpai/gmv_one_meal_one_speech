document.addEventListener("DOMContentLoaded", () => {
  const navToggle = document.querySelector(".nav-toggle");
  const navigation = document.querySelector("#main-navigation");
  if (navToggle && navigation) {
    navToggle.addEventListener("click", () => {
      const isOpen = navigation.classList.toggle("open");
      navToggle.setAttribute("aria-expanded", String(isOpen));
    });
  }

  document.querySelectorAll("[data-countdown]").forEach((countdown) => {
    const target = new Date(countdown.dataset.countdown).getTime();
    const renderCountdown = () => {
      const remaining = Math.max(0, target - Date.now());
      const totalSeconds = Math.floor(remaining / 1000);
      const totalDays = Math.floor(totalSeconds / 86400);
      const units = {
        months: Math.floor(totalDays / 30),
        days: totalDays % 30,
        hours: Math.floor((totalSeconds % 86400) / 3600),
        minutes: Math.floor((totalSeconds % 3600) / 60),
        seconds: totalSeconds % 60,
      };
      Object.entries(units).forEach(([unit, value]) => {
        const targetElement = countdown.querySelector(`[data-unit="${unit}"]`);
        if (targetElement) targetElement.textContent = String(value).padStart(2, "0");
      });
    };
    renderCountdown();
    window.setInterval(renderCountdown, 1000);
  });

  document.querySelectorAll("[data-carousel]").forEach((carousel) => {
    const slides = [...carousel.querySelectorAll("[data-slide]")];
    const dots = [...carousel.querySelectorAll("[data-go-to]")];
    if (slides.length < 2) return;
    let activeIndex = 0;
    const showSlide = (index) => {
      activeIndex = (index + slides.length) % slides.length;
      slides.forEach((slide, position) => slide.classList.toggle("active", position === activeIndex));
      dots.forEach((dot, position) => dot.classList.toggle("active", position === activeIndex));
    };
    carousel.querySelector("[data-previous]")?.addEventListener("click", () => showSlide(activeIndex - 1));
    carousel.querySelector("[data-next]")?.addEventListener("click", () => showSlide(activeIndex + 1));
    dots.forEach((dot) => dot.addEventListener("click", () => showSlide(Number(dot.dataset.goTo))));
  });

  document.querySelectorAll("[data-file-input]").forEach((input) => {
    input.addEventListener("change", () => {
      const output = document.querySelector(input.dataset.fileList || "[data-file-list]");
      if (!output) return;
      const files = [...input.files];
      output.textContent = files.length ? files.map((file) => file.name).join(" · ") : "";
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = document.getElementById(button.dataset.copy)?.textContent?.trim();
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        button.textContent = "Copiado";
        window.setTimeout(() => { button.textContent = "Copiar"; }, 1800);
      } catch (_error) {
        button.textContent = "Selecciona y copia";
      }
    });
  });
});
