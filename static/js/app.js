(() => {
  const clock = document.getElementById("liveClock");
  const selectedStudentId = window.JEVICARN?.selectedStudentId ?? null;
  const installBtn = document.getElementById("installBtn");
  const sidebarDrawer = document.getElementById("sidebarDrawer");
  const sidebarBackdrop = document.getElementById("sidebarBackdrop");
  const sidebarToggle = document.getElementById("sidebarToggle");
  const sidebarClose = document.getElementById("sidebarClose");
  let deferredPrompt = null;

  const panels = Array.from(document.querySelectorAll(".workspace-panel"));
  const defaultPanel = document.getElementById("students-panel");

  function pad(n) { return String(n).padStart(2, "0"); }
  function renderClock() {
    if (!clock) return;
    const d = new Date();
    const datePart = d.toLocaleDateString(undefined, {
      weekday: "long",
      year: "numeric",
      month: "short",
      day: "numeric",
    });
    clock.textContent = `${datePart} • ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
  renderClock();
  setInterval(renderClock, 1000);

  function openSidebar() {
    if (!sidebarDrawer || !sidebarBackdrop) return;
    sidebarDrawer.classList.add("open");
    sidebarBackdrop.hidden = false;
    requestAnimationFrame(() => sidebarBackdrop.classList.add("show"));
  }

  function closeSidebar() {
    if (!sidebarDrawer || !sidebarBackdrop) return;
    sidebarDrawer.classList.remove("open");
    sidebarBackdrop.classList.remove("show");
    window.setTimeout(() => {
      if (!sidebarDrawer.classList.contains("open")) sidebarBackdrop.hidden = true;
    }, 180);
  }

  function showPanel(panelId, scrollTarget) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    panels.forEach((p) => p.classList.add("hidden"));
    panel.classList.remove("hidden");
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    if (scrollTarget) {
      window.setTimeout(() => {
        const target = document.getElementById(scrollTarget);
        target?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 120);
    }
  }

  function activateFromButton(button) {
    const panelId = button.dataset.target;
    const scrollTarget = button.dataset.scroll;
    if (panelId) showPanel(panelId, scrollTarget);
    if (window.matchMedia("(max-width: 900px)").matches) closeSidebar();
  }

  sidebarToggle?.addEventListener("click", openSidebar);
  sidebarClose?.addEventListener("click", closeSidebar);
  sidebarBackdrop?.addEventListener("click", closeSidebar);

  document.querySelectorAll(".nav-group-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const group = button.closest(".nav-group");
      if (!group) return;
      const willOpen = !group.classList.contains("open");
      group.classList.toggle("open", willOpen);
      button.setAttribute("aria-expanded", String(willOpen));
      activateFromButton(button);
    });
  });

  document.querySelectorAll(".nav-action").forEach((button) => {
    button.addEventListener("click", () => activateFromButton(button));
  });

  document.querySelectorAll(".side-nav a").forEach((link) => {
    link.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 900px)").matches) closeSidebar();
    });
  });

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    if (installBtn) installBtn.hidden = false;
  });
  installBtn?.addEventListener("click", async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    installBtn.hidden = true;
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", async () => {
      try {
        await navigator.serviceWorker.register("/sw.js");
      } catch (err) {
        console.warn("SW registration failed", err);
      }
    });
  }

  const modal = document.getElementById("studentModal");
  const closeBtn = document.getElementById("closeStudentModal");
  const openBtn = document.getElementById("openStudentBtn");
  const modalEls = {
    name: document.getElementById("modalStudentName"),
    admission: document.getElementById("modalAdmission"),
    grade: document.getElementById("modalGrade"),
    status: document.getElementById("modalStatus"),
    balance: document.getElementById("modalBalance"),
    paymentsBody: document.getElementById("modalPaymentsBody"),
  };

  function renderPaymentRows(payments = []) {
    if (!modalEls.paymentsBody) return;
    modalEls.paymentsBody.innerHTML = payments.length
      ? payments.map(p => `
        <tr>
          <td>${(p.created_at || "").slice(0, 16)}</td>
          <td>KES ${Number(p.amount || 0).toLocaleString()}</td>
          <td>${p.method || ""}</td>
          <td>${p.recorded_by_name || ""}</td>
          <td><span class="pill ${String(p.status || "").toLowerCase()}">${p.status || ""}</span></td>
        </tr>
      `).join("")
      : `<tr><td colspan="5" class="muted">No payments found.</td></tr>`;
  }

  async function loadStudent(studentId) {
    if (!studentId) return;
    try {
      const res = await fetch(`/api/student/${studentId}`);
      if (!res.ok) throw new Error("Failed to load student");
      const data = await res.json();
      const s = data.student;
      modalEls.name.textContent = s.full_name;
      modalEls.admission.textContent = s.admission_no;
      modalEls.grade.textContent = s.grade;
      modalEls.status.textContent = s.payment_status;
      modalEls.balance.textContent = "KES " + Number(s.balance || 0).toLocaleString();
      renderPaymentRows(data.payments || []);
      modal?.showModal();
    } catch (err) {
      console.warn(err);
    }
  }

  document.querySelectorAll(".js-view-student").forEach(btn => {
    btn.addEventListener("click", () => loadStudent(btn.dataset.studentId));
  });
  openBtn?.addEventListener("click", () => {
    if (selectedStudentId) loadStudent(selectedStudentId);
  });
  closeBtn?.addEventListener("click", () => modal?.close());
  modal?.addEventListener("click", (e) => {
    const rect = modal.querySelector(".modal-card")?.getBoundingClientRect();
    if (!rect) return;
    const inDialog = (
      e.clientX >= rect.left &&
      e.clientX <= rect.right &&
      e.clientY >= rect.top &&
      e.clientY <= rect.bottom
    );
    if (!inDialog) modal.close();
  });

  document.querySelectorAll(".student-row").forEach(row => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("button, form, a")) return;
      loadStudent(row.dataset.studentId);
    });
  });

  document.querySelectorAll("form").forEach(form => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("button[type='submit']");
      if (btn) {
        btn.disabled = true;
        setTimeout(() => btn.disabled = false, 3000);
      }
    });
  });

  if (defaultPanel) panels.forEach((p) => p !== defaultPanel && p.classList.add("hidden"));
})();
